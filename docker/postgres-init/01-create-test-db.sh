#!/bin/sh
# Cria o banco de testes ao lado do banco principal.
# Só roda quando o volume está vazio (primeira inicialização do container).
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<EOSQL
CREATE DATABASE "${POSTGRES_DB}_test";
EOSQL
