#!/bin/sh
# Bootstrap for DEV Postgres (docker compose and the chart's bundled `postgresql.bundled` StatefulSet).
# Production uses an external managed Postgres: create the same owner role and database there.
#
#   slm_migrator  LOGIN CREATEROLE, owns database `slm`: the identity db/migrate.py runs as. migrate.py itself
#                 creates the runtime role `slm_app` (NOSUPERUSER NOBYPASSRLS, so row-level security applies)
#                 with the password from SLM_APP_PASSWORD and re-applies its grants on every run.
#
# Required env: POSTGRES_USER, SLM_MIGRATOR_DB_PASSWORD
# Optional env: MLFLOW_DB_PASSWORD (creates database `mlflow` + role `mlflow`)
set -eu

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
     -v mig_pw="$SLM_MIGRATOR_DB_PASSWORD" <<'EOSQL'
CREATE ROLE slm_migrator LOGIN PASSWORD :'mig_pw' NOSUPERUSER CREATEROLE NOBYPASSRLS;
CREATE DATABASE slm OWNER slm_migrator;
REVOKE ALL ON DATABASE slm FROM PUBLIC;
EOSQL

if [ -n "${MLFLOW_DB_PASSWORD:-}" ]; then
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres -v ml_pw="$MLFLOW_DB_PASSWORD" <<'EOSQL'
CREATE ROLE mlflow LOGIN PASSWORD :'ml_pw' NOSUPERUSER;
CREATE DATABASE mlflow OWNER mlflow;
EOSQL
fi
