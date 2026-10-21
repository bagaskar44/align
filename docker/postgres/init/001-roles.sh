#!/usr/bin/env bash
set -euo pipefail

psql --set=ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
\getenv migration_password MIGRATION_DB_PASSWORD
\getenv api_password API_DB_PASSWORD
\getenv worker_password WORKER_DB_PASSWORD
\getenv operator_password OPERATOR_DB_PASSWORD

SELECT format('CREATE ROLE align_migration LOGIN PASSWORD %L', :'migration_password') \gexec
SELECT format('CREATE ROLE align_api LOGIN PASSWORD %L', :'api_password') \gexec
SELECT format('CREATE ROLE align_worker LOGIN PASSWORD %L', :'worker_password') \gexec
SELECT format('CREATE ROLE align_operator LOGIN PASSWORD %L', :'operator_password') \gexec

CREATE EXTENSION IF NOT EXISTS vector;
SELECT format('GRANT CONNECT ON DATABASE %I TO align_migration, align_api, align_worker, align_operator', current_database()) \gexec
ALTER SCHEMA public OWNER TO align_migration;
GRANT USAGE ON SCHEMA public TO align_api, align_worker, align_operator;
ALTER DEFAULT PRIVILEGES FOR ROLE align_migration IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO align_api;
ALTER DEFAULT PRIVILEGES FOR ROLE align_migration IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO align_api;
SQL
