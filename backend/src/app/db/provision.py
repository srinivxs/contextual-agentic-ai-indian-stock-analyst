"""Create the no-DDL runtime role in a database that never ran the local init script.

WHY THIS EXISTS
    ADR 011 gives the project two database roles: a privileged migration role that owns the schema,
    and a runtime role (``stock_app``) that the API connects as and that cannot create, alter or
    drop anything. Locally, ``docker/postgres-init/01-roles-and-databases.sh`` creates the runtime
    role and its grants once, when the database volume is first created.

    RDS has no such hook. It gives us one database and one master user, and nothing else. Terraform
    cannot fill the gap either: the instance lives in isolated subnets with no public access, so
    nothing outside the VPC can run SQL against it. The same SQL therefore runs from *inside* the
    VPC, as a one-off ECS task, using the migration role -- the sibling of the task that runs
    ``alembic upgrade head`` (infra/stack/compute.tf).

WHERE THE PASSWORD COMES FROM
    Not from Terraform. The runtime password is generated ephemerally and written straight into SSM
    as a write-only attribute, so after the apply nobody -- including Terraform -- can read it back
    (infra/stack/secrets.tf). The one place it exists is that parameter, which ECS injects into this
    task as ``DATABASE_URL``. So this module reads the password out of the URL it is given, and
    applies it as the migration role.

WHY THE STATEMENTS ARE BUILT AS STRINGS
    PostgreSQL does not accept bound parameters in DDL: a role name and a password in
    ``CREATE ROLE`` have to be part of the statement text. Both are therefore validated
    against a strict allow-list before they are interpolated, and anything that does not
    match is refused outright rather than escaped. That is affordable here because both
    values are ours: the role name is a fixed identifier, and the password is alphanumeric
    by construction (``special = false``).

WHY IT CAN RUN IN EITHER ORDER, ANY NUMBER OF TIMES
    ``ALTER DEFAULT PRIVILEGES`` only affects tables created after it runs, and ``ON ALL TABLES``
    only affects tables that exist now. Doing both means this task and the migration can run in
    either order, and either can be re-run, without anyone having to remember which. An existing
    role is altered rather than created, so a rotated password takes effect on the next run.
"""

import asyncio
import logging
import os
import re
from dataclasses import dataclass, field

from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.core.logging import configure_logging

logger = logging.getLogger(__name__)

# A plain lower-case SQL identifier: what every role and database in this project is called.
# Anything else is a mistake or an attack, and either way it must not reach a DDL statement.
_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")

# The shape `random_password` produces with `special = false`. Deliberately narrow: a password that
# needs quoting is a password we did not generate.
_GENERATED_PASSWORD = re.compile(r"^[A-Za-z0-9]{16,128}$")


@dataclass(frozen=True)
class RuntimeRole:
    """The role to create, read out of the runtime connection URL."""

    name: str
    # repr=False so that printing this by accident -- in a traceback, a log line, a debugger --
    # does not print the password with it.
    password: str = field(repr=False)
    database: str


def _identifier(value: str | None, what: str) -> str:
    if not value:
        raise ValueError(f"The connection URL carries no {what}.")
    if not _IDENTIFIER.fullmatch(value):
        # The value is echoed because it is a name, not a secret, and naming it is the whole point.
        raise ValueError(
            f"The {what} {value!r} is not a plain identifier, so it cannot go into DDL."
        )
    return value


def _generated_password(value: str | None) -> str:
    if not value:
        raise ValueError("The connection URL carries no password.")
    if not _GENERATED_PASSWORD.fullmatch(value):
        # Never echoed: unlike a role name, this one is a secret.
        raise ValueError(
            "The password in the connection URL is not the alphanumeric value this project "
            "generates (infra/stack/secrets.tf sets special = false), so it will not be "
            "interpolated into a statement."
        )
    return value


def runtime_role_from_url(url: str) -> RuntimeRole:
    """Read the role to create out of the URL the API will later connect with."""
    parsed = make_url(url)
    return RuntimeRole(
        name=_identifier(parsed.username, "role name"),
        password=_generated_password(parsed.password),
        database=_identifier(parsed.database, "database name"),
    )


def plan(*, migration_url: str, runtime_url: str, role_exists: bool) -> tuple[str, ...]:
    """The statements to run, in order. Pure: it touches nothing and is fully unit-tested."""
    role = runtime_role_from_url(runtime_url)

    admin = make_url(migration_url)
    admin_role = _identifier(admin.username, "migration role name")
    admin_database = _identifier(admin.database, "migration database name")

    if admin_database != role.database:
        # A grant made in the wrong database is not an error, it is a silent no-op: everything
        # succeeds and the API still cannot read a single row.
        raise ValueError(
            f"The two URLs name different databases ({admin_database!r} and {role.database!r}). "
            "Grants are per database, so this would appear to succeed and change nothing."
        )

    # ALTER, not CREATE, when it is already there, so a rotated password takes effect.
    #
    # The ALTER path sets ONLY the password, and that is not an oversight. On RDS the migration
    # role is `rds_superuser`, which is not a superuser, and PostgreSQL refuses
    # `ALTER ROLE ... NOSUPERUSER` from anyone who is not one ("Only roles with the SUPERUSER
    # attribute may change the SUPERUSER attribute"); NOREPLICATION is restricted the same way.
    # CREATE may state them, because creating a role without an attribute is not *changing* that
    # attribute. Restating them on ALTER made the second run fail on real RDS while passing
    # locally, where the Compose admin user really is a superuser.
    #
    # What we cannot always set, we check instead: see privilege_problems below.
    role_statement = (
        f"ALTER ROLE \"{role.name}\" WITH LOGIN PASSWORD '{role.password}'"
        if role_exists
        else (
            f'CREATE ROLE "{role.name}" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION '
            f"PASSWORD '{role.password}'"
        )
    )

    return (
        role_statement,
        # Already the default on PostgreSQL 15+. Stated so the intent is visible and does not
        # depend on the version, exactly as the local init script does.
        "REVOKE CREATE ON SCHEMA public FROM PUBLIC",
        f'GRANT CONNECT ON DATABASE "{role.database}" TO "{role.name}"',
        f'GRANT USAGE ON SCHEMA public TO "{role.name}"',
        # For the tables the migration has already created...
        f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "{role.name}"',
        f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO "{role.name}"',
        # ...and for the ones it creates later. "FOR ROLE" must name the role that will own those
        # tables, or this applies to nothing and nobody finds out until a query fails.
        f'ALTER DEFAULT PRIVILEGES FOR ROLE "{admin_role}" IN SCHEMA public '
        f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "{role.name}"',
        f'ALTER DEFAULT PRIVILEGES FOR ROLE "{admin_role}" IN SCHEMA public '
        f'GRANT USAGE, SELECT ON SEQUENCES TO "{role.name}"',
    )


ROLE_ATTRIBUTES = (
    "SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolcanlogin "
    "FROM pg_roles WHERE rolname = :name"
)


def privilege_problems(
    *,
    rolsuper: bool,
    rolcreatedb: bool,
    rolcreaterole: bool,
    rolreplication: bool,
    rolcanlogin: bool,
) -> tuple[str, ...]:
    """What is wrong with the runtime role's attributes, in plain words.

    The statements above cannot guarantee all of these on every database -- see the note on the
    ALTER path -- so they are read back and reported. A boundary we cannot enforce is one we must
    at least refuse to claim.
    """
    problems = []
    if rolsuper:
        problems.append("it is a superuser")
    if rolcreatedb:
        problems.append("it can create databases")
    if rolcreaterole:
        problems.append("it can create roles")
    if rolreplication:
        problems.append("it can replicate")
    if not rolcanlogin:
        problems.append("it cannot log in, so the application could not use it")
    return tuple(problems)


def raise_if_widened(role_name: str, problems: tuple[str, ...]) -> None:
    """Refuse a role we cannot vouch for, naming everything that is wrong with it."""
    if not problems:
        return
    raise RuntimeError(
        f"The runtime role {role_name!r} is not what it must be: {'; '.join(problems)}. "
        "Some attributes cannot be taken away by a role that is not a real superuser (on RDS "
        "the master user is rds_superuser), so this has to be put right by hand."
    )


async def provision(*, migration_url: str, runtime_url: str) -> None:
    """Connect as the migration role and make the runtime role match the runtime URL."""
    role = runtime_role_from_url(runtime_url)

    # NullPool: this process runs a handful of statements and exits, so it needs one connection.
    engine = create_async_engine(migration_url, poolclass=NullPool)
    try:
        # PostgreSQL has transactional DDL, so either the role and all of its grants exist
        # afterwards, or none of it does. There is no half-provisioned state to clean up.
        async with engine.begin() as connection:
            found = await connection.execute(
                text("SELECT 1 FROM pg_roles WHERE rolname = :name"), {"name": role.name}
            )
            statements = plan(
                migration_url=migration_url,
                runtime_url=runtime_url,
                role_exists=found.first() is not None,
            )
            for statement in statements:
                await connection.execute(text(statement))

            attributes = (
                await connection.execute(text(ROLE_ATTRIBUTES), {"name": role.name})
            ).one()
            problems = privilege_problems(
                rolsuper=attributes.rolsuper,
                rolcreatedb=attributes.rolcreatedb,
                rolcreaterole=attributes.rolcreaterole,
                rolreplication=attributes.rolreplication,
                rolcanlogin=attributes.rolcanlogin,
            )
            # Inside the transaction, so the password change rolls back with it: a role we
            # cannot vouch for does not quietly become the one the application uses.
            raise_if_widened(role.name, problems)
    finally:
        await engine.dispose()

    logger.info("runtime role provisioned", extra={"role": role.name, "database": role.database})


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        # A one-off task fails by exiting, so the reason has to be in the log. The variable is
        # named; its value never is.
        raise SystemExit(f"{name} is not set. ECS injects it from SSM; see infra/stack/compute.tf.")
    return value


def main() -> None:
    configure_logging()
    migration_url = _required("MIGRATION_DATABASE_URL")
    runtime_url = _required("DATABASE_URL")
    asyncio.run(provision(migration_url=migration_url, runtime_url=runtime_url))


if __name__ == "__main__":  # pragma: no cover - exercised by the ECS task, not by pytest
    main()
