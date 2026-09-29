#!/bin/sh
# Creates the test database next to the main one.
# Runs only when the volume is empty (first container start).
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<EOSQL
CREATE DATABASE "${POSTGRES_DB}_test";
EOSQL
