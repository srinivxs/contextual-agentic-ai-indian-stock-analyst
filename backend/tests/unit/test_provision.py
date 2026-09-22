"""The SQL that creates the no-DDL runtime role, as pure logic.

WHY THIS MODULE EXISTS AT ALL
    Locally, ``docker/postgres-init/01-roles-and-databases.sh`` creates the runtime role and its
    grants once, when the database volume is first created. RDS has no such hook: it gives us one
    database and one master user and nothing else, and Terraform cannot run SQL against it because
    the instance sits in isolated subnets with no public access (ADR 011). So the same SQL has to
    run from inside the VPC, as a one-off task, using the migration role.

WHY THE STATEMENTS ARE BUILT AS STRINGS
    A role name and a password cannot be bound parameters: PostgreSQL does not accept placeholders
    in DDL. Both are therefore interpolated, and both are validated against a strict allow-list
    first. The password is alphanumeric by construction (``special = false`` in
    ``infra/stack/secrets.tf``), so the allow-list costs nothing and removes every quoting question.

WHY BOTH ``ON ALL TABLES`` AND ``ALTER DEFAULT PRIVILEGES``
    ``ALTER DEFAULT PRIVILEGES`` only affects tables created *after* it runs, and ``ON ALL TABLES``
    only affects tables that exist *now*. Doing both means this task and ``alembic upgrade head``
    can run in either order, and either can be re-run, without anyone having to remember which.
"""

import pytest

from app.db.provision import (
    RuntimeRole,
    main,
    plan,
    privilege_problems,
    raise_if_widened,
    runtime_role_from_url,
)

# 32 alphanumerics, the shape `random_password` with `special = false` produces.
CREDENTIAL = "K7mQx2vB9nR4tL6wZ1cY8jH3sD5fG0pA"
RUNTIME_URL = f"postgresql+asyncpg://stock_app:{CREDENTIAL}@db.example.com:5432/stock_analyst"
MIGRATION_URL = (
    f"postgresql+asyncpg://stock_admin:{CREDENTIAL}@db.example.com:5432/stock_analyst?ssl=require"
)


# --- reading the role out of the connection URL ---------------------------------------------------


def test_the_runtime_role_is_read_out_of_the_url() -> None:
    """Terraform cannot tell us the password: it is write-only and unreadable after the apply.

    The one place it exists is the SSM parameter, which ECS injects as DATABASE_URL. So the URL is
    the input, and the role, the password and the database are read back out of it.
    """
    role = runtime_role_from_url(RUNTIME_URL)

    assert role == RuntimeRole(name="stock_app", password=CREDENTIAL, database="stock_analyst")


def test_the_url_may_carry_the_ssl_parameter_rds_requires() -> None:
    """The parameter group sets rds.force_ssl = 1, so every deployed URL ends in ?ssl=require."""
    role = runtime_role_from_url(f"{RUNTIME_URL}?ssl=require")

    assert role.database == "stock_analyst"
    assert role.password == CREDENTIAL


def test_the_password_is_kept_out_of_the_repr() -> None:
    """Anything that prints a dataclass by accident must not print the password with it."""
    role = runtime_role_from_url(RUNTIME_URL)

    assert CREDENTIAL not in repr(role)
    assert "stock_app" in repr(role)


@pytest.mark.parametrize(
    "url",
    [
        pytest.param("postgresql+asyncpg://stock_app@db.example.com/stock_analyst", id="password"),
        pytest.param(f"postgresql+asyncpg://:{CREDENTIAL}@db.example.com/stock_analyst", id="role"),
        pytest.param(f"postgresql+asyncpg://stock_app:{CREDENTIAL}@db.example.com", id="database"),
    ],
)
def test_a_url_missing_a_part_we_need_is_refused(url: str) -> None:
    with pytest.raises(ValueError, match=r"role name|password|database"):
        runtime_role_from_url(url)


def test_a_role_name_that_is_not_a_plain_identifier_is_refused() -> None:
    """The name goes into DDL unbound, so anything but a plain identifier is rejected outright."""
    hostile = 'stock_app" SUPERUSER; --'

    with pytest.raises(ValueError, match="role name"):
        runtime_role_from_url(f"postgresql+asyncpg://{hostile}:{CREDENTIAL}@h/stock_analyst")


def test_a_password_with_characters_we_never_generate_is_refused() -> None:
    """`special = false` means alphanumeric. A quote in there means something is wrong upstream."""
    with pytest.raises(ValueError, match="password") as caught:
        runtime_role_from_url("postgresql+asyncpg://stock_app:has'quote@h/stock_analyst")

    assert "has'quote" not in str(caught.value), "the error must not echo the password"


# --- the statements -------------------------------------------------------------------------------


def test_a_missing_role_is_created_with_no_privileges_of_its_own() -> None:
    statements = plan(migration_url=MIGRATION_URL, runtime_url=RUNTIME_URL, role_exists=False)

    assert statements[0] == (
        'CREATE ROLE "stock_app" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION '
        f"PASSWORD '{CREDENTIAL}'"
    )


def test_an_existing_role_is_altered_instead_so_a_rotated_password_takes_effect() -> None:
    """Raising db_password_version writes a new password into SSM. Re-running must apply it."""
    statements = plan(migration_url=MIGRATION_URL, runtime_url=RUNTIME_URL, role_exists=True)

    assert statements[0] == f"ALTER ROLE \"stock_app\" WITH LOGIN PASSWORD '{CREDENTIAL}'"


def test_the_alter_path_never_restates_an_attribute_rds_will_not_let_us_change() -> None:
    """Found by the first real drill, not by any local test.

    On RDS the migration role is `rds_superuser`, which is NOT a superuser, and PostgreSQL refuses
    `ALTER ROLE ... NOSUPERUSER` from anyone who is not one: "Only roles with the SUPERUSER
    attribute may change the SUPERUSER attribute". NOREPLICATION is restricted the same way. The
    CREATE path may state them, because creating a role without an attribute is not *changing* that
    attribute -- which is why the first run succeeded and the second failed.

    So the ALTER path sets only what it is actually there to set: the password. The attributes are
    verified afterwards instead (see privilege_problems).
    """
    statements = plan(migration_url=MIGRATION_URL, runtime_url=RUNTIME_URL, role_exists=True)

    for attribute in ("SUPERUSER", "REPLICATION", "CREATEDB", "CREATEROLE"):
        assert attribute not in statements[0], statements[0]


# --- checking the attributes we cannot always set -------------------------------------------------


def test_a_correct_role_has_no_problems() -> None:
    assert (
        privilege_problems(
            rolsuper=False,
            rolcreatedb=False,
            rolcreaterole=False,
            rolreplication=False,
            rolcanlogin=True,
        )
        == ()
    )


@pytest.mark.parametrize(
    ("attribute", "phrase"),
    [
        ("rolsuper", "superuser"),
        ("rolcreatedb", "create databases"),
        ("rolcreaterole", "create roles"),
        ("rolreplication", "replicate"),
    ],
)
def test_every_widened_attribute_is_reported(attribute: str, phrase: str) -> None:
    """We cannot always take these away, but we can refuse to pretend the boundary exists."""
    flags = {
        "rolsuper": False,
        "rolcreatedb": False,
        "rolcreaterole": False,
        "rolreplication": False,
        "rolcanlogin": True,
    }
    flags[attribute] = True

    problems = privilege_problems(**flags)

    assert len(problems) == 1
    assert phrase in problems[0]


def test_a_role_that_cannot_log_in_is_reported() -> None:
    problems = privilege_problems(
        rolsuper=False,
        rolcreatedb=False,
        rolcreaterole=False,
        rolreplication=False,
        rolcanlogin=False,
    )

    assert len(problems) == 1
    assert "log in" in problems[0]


def test_the_password_appears_in_no_other_statement() -> None:
    statements = plan(migration_url=MIGRATION_URL, runtime_url=RUNTIME_URL, role_exists=False)

    assert all(CREDENTIAL not in statement for statement in statements[1:])


def test_the_grants_are_the_same_ones_the_local_init_script_sets() -> None:
    statements = plan(migration_url=MIGRATION_URL, runtime_url=RUNTIME_URL, role_exists=False)

    assert set(statements[1:]) == {
        "REVOKE CREATE ON SCHEMA public FROM PUBLIC",
        'GRANT CONNECT ON DATABASE "stock_analyst" TO "stock_app"',
        'GRANT USAGE ON SCHEMA public TO "stock_app"',
        'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "stock_app"',
        'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO "stock_app"',
        'ALTER DEFAULT PRIVILEGES FOR ROLE "stock_admin" IN SCHEMA public '
        'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "stock_app"',
        'ALTER DEFAULT PRIVILEGES FOR ROLE "stock_admin" IN SCHEMA public '
        'GRANT USAGE, SELECT ON SEQUENCES TO "stock_app"',
    }


def test_the_role_is_never_granted_ddl() -> None:
    """The whole point of ADR 011: a compromised API container cannot reshape the schema."""
    statements = plan(migration_url=MIGRATION_URL, runtime_url=RUNTIME_URL, role_exists=False)

    forbidden = ("CREATEDB", "CREATEROLE", "SUPERUSER", "TRUNCATE", "CREATE ON SCHEMA public TO")
    for statement in statements[1:]:
        assert not any(word in statement for word in forbidden), statement


def test_the_default_privileges_name_the_migration_role_from_the_admin_url() -> None:
    """`ALTER DEFAULT PRIVILEGES FOR ROLE x` only covers tables x itself creates.

    Naming the wrong role would produce a silent no-op: everything applies cleanly and the runtime
    role still cannot read the next migration's tables.
    """
    statements = plan(
        migration_url=f"postgresql+asyncpg://rds_master:{CREDENTIAL}@h/stock_analyst",
        runtime_url=RUNTIME_URL,
        role_exists=False,
    )

    defaults = [s for s in statements if s.startswith("ALTER DEFAULT PRIVILEGES")]
    assert len(defaults) == 2, "one for tables, one for sequences"
    # `all`, not `any`: hardcoding one of the two would leave the other one right and hide it.
    assert all('FOR ROLE "rds_master"' in statement for statement in defaults)


def test_two_urls_pointing_at_different_databases_are_refused() -> None:
    """A grant made in the wrong database is invisible and useless. Fail before touching it."""
    with pytest.raises(ValueError, match="database"):
        plan(
            migration_url=f"postgresql+asyncpg://stock_admin:{CREDENTIAL}@h/somewhere_else",
            runtime_url=RUNTIME_URL,
            role_exists=False,
        )


def test_a_migration_role_that_is_not_a_plain_identifier_is_refused() -> None:
    with pytest.raises(ValueError, match="role name"):
        plan(
            migration_url=f'postgresql+asyncpg://bad" name:{CREDENTIAL}@h/stock_analyst',
            runtime_url=RUNTIME_URL,
            role_exists=False,
        )


# --- the entrypoint the ECS task runs -------------------------------------------------------------


def test_main_names_the_variable_it_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """The failure mode of a one-off task is a container that exits; the log must say why."""
    monkeypatch.delenv("MIGRATION_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", RUNTIME_URL)

    with pytest.raises(SystemExit, match="MIGRATION_DATABASE_URL"):
        main()


def test_main_names_the_other_variable_too(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIGRATION_DATABASE_URL", MIGRATION_URL)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(SystemExit, match="DATABASE_URL"):
        main()


def test_main_hands_both_urls_to_provision(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, str] = {}

    async def fake_provision(*, migration_url: str, runtime_url: str) -> None:
        seen["migration"] = migration_url
        seen["runtime"] = runtime_url

    monkeypatch.setattr("app.db.provision.provision", fake_provision)
    monkeypatch.setenv("MIGRATION_DATABASE_URL", MIGRATION_URL)
    monkeypatch.setenv("DATABASE_URL", RUNTIME_URL)

    main()

    assert seen == {"migration": MIGRATION_URL, "runtime": RUNTIME_URL}


def test_a_role_with_no_problems_is_accepted_silently() -> None:
    raise_if_widened("stock_app", ())  # must not raise


def test_a_widened_role_is_refused_with_every_problem_named() -> None:
    """It names all of them, because a hand fix should only have to be made once."""
    with pytest.raises(RuntimeError) as caught:
        raise_if_widened("stock_app", ("it is a superuser", "it can create roles"))

    message = str(caught.value)
    assert "stock_app" in message
    assert "it is a superuser" in message
    assert "it can create roles" in message
    assert "rds_superuser" in message, "the message should say why we could not fix it ourselves"
