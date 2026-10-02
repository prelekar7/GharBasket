-- Runs once, on the first start of an empty data volume, as the superuser.
-- The main database (POSTGRES_DB, default "gharbasket") is created by the image.

CREATE DATABASE gharbasket_test;
CREATE DATABASE mlflow;

-- Extensions needed by the application schemas (see docs/handbook/05-data-architecture.md).
\connect gharbasket
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

\connect gharbasket_test
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
