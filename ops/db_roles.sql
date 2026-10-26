-- Production database roles. Run once per environment as a superuser, after the
-- migrations. Not applied automatically: roles are cluster-wide.
--
-- calipa_migrator owns the schema and runs migrations.
-- calipa_app is what the service connects as: it can read and insert, nothing else.
-- The append-only triggers stay in place as a second line of defence.

create role calipa_migrator login;
create role calipa_app login;

grant usage on schema public to calipa_app;
grant select, insert on all tables in schema public to calipa_app;
revoke update, delete, truncate on all tables in schema public from calipa_app;

alter default privileges for role calipa_migrator in schema public
    grant select, insert on tables to calipa_app;
