# PostgreSQL 18 with PostGIS and pgvector for local development.
# Built from the official multi-arch image because postgis/postgis:18 is amd64-only
# and would run under slow emulation on Apple Silicon.
# pg_trgm, unaccent and pgcrypto ship with postgres contrib.
FROM postgres:18.6-trixie

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        postgresql-18-postgis-3 \
        postgresql-18-pgvector \
    && rm -rf /var/lib/apt/lists/*

COPY initdb/ /docker-entrypoint-initdb.d/
