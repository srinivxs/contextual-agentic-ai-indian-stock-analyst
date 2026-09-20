#!/bin/bash
# Runs once, when the database volume is first created (docker-entrypoint-initdb.d).
#
# Creates the two-role boundary described in docs/decisions (ADR 011, written in P3b):
#
#   MIGRATION role  = $POSTGRES_USER (made by the image, a superuser). Creates the pgvector
#                     extension and tables. On AWS this is the RDS master user, used only by the
#                     one-off migration task.
#   RUNTIME role    = $APP_DB_USER (made here). What the API and worker connect as. It can read and
#                     write rows, but it has NO DDL rights: it cannot create, alter or drop tables.
#
# It also creates a second database, stock_analyst_test, so integration tests never touch dev data.
# Local development only: the password is passed on a command line inside the container.
set -euo pipefail

TEST_DB="stock_analyst_test"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
  -v app_user="$APP_DB_USER" -v app_password="$APP_DB_PASSWORD" \
  -v admin_user="$POSTGRES_USER" -v test_db="$TEST_DB" <<'SQL'
CREATE ROLE :"app_user" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION
    PASSWORD :'app_password';
CREATE DATABASE :"test_db" OWNER :"admin_user";
SQL

for db in "$POSTGRES_DB" "$TEST_DB"; do
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$db" \
    -v app_user="$APP_DB_USER" -v admin_user="$POSTGRES_USER" -v db_name="$db" <<'SQL'
-- Nobody but the owner may create objects in the public schema (already the default on
-- PostgreSQL 15+; stated explicitly so the intent is visible and does not depend on the version).
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

GRANT CONNECT ON DATABASE :"db_name" TO :"app_user";
GRANT USAGE ON SCHEMA public TO :"app_user";

-- Tables the migration role creates from now on are readable and writable by the runtime role,
-- and nothing more (no TRUNCATE, no ALTER, no DROP). Sequences are needed for identity columns.
ALTER DEFAULT PRIVILEGES FOR ROLE :"admin_user" IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO :"app_user";
ALTER DEFAULT PRIVILEGES FOR ROLE :"admin_user" IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO :"app_user";
SQL
done
