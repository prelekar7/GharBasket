-- The read-only role of the training jobs (ADR-0025; backend/training reads with DATABASE_URL_RO).
-- Run by scripts/neon-ro-role.sh as the database owner (the role the migrations run as), with
-- psql variables `role` and `password`. Safe to run again: it re-keys the role and re-grants.
--
-- Least privilege: only the schemas the jobs read (snapshots, features, forecast, ranker, pantry
-- refit), and from identity only the two household tables, never users, sessions, addresses or
-- consents. identity.household_members holds health tags (DPDP: collected with consent, used for
-- planning); refit_pantry reads member age bands from it, so the table is granted whole. The
-- snapshots it writes to the lake carry no personal data (backend/training snapshots.py).

SELECT format('CREATE ROLE %I LOGIN', :'role')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'role') \gexec

-- Only LOGIN and the password: Neon's owner is not a superuser, so it may not name SUPERUSER (or
-- the other attributes it lacks); a new role has none of them anyway.
ALTER ROLE :"role" WITH LOGIN PASSWORD :'password';
-- Read-only even if a grant slips: every transaction of the role starts READ ONLY.
ALTER ROLE :"role" SET default_transaction_read_only = on;
ALTER ROLE :"role" SET statement_timeout = '10min';

SELECT format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), :'role') \gexec

GRANT USAGE ON SCHEMA catalog, subscriptions, orders, intel, identity TO :"role";
GRANT SELECT ON ALL TABLES IN SCHEMA catalog, subscriptions, orders, intel TO :"role";
GRANT SELECT ON identity.households, identity.household_members TO :"role";
-- Tables later migrations add to these schemas (created by this owner) are readable too.
ALTER DEFAULT PRIVILEGES IN SCHEMA catalog, subscriptions, orders, intel
    GRANT SELECT ON TABLES TO :"role";
