-- Step outputs (approved scope, GL snapshot, engine result, ...) stored as canonical
-- JSON blobs. Later steps read their inputs back from here, never from memory, so a
-- run can resume after a crash and a report is built only from persisted facts.

alter table runs add column parent_run_id uuid references runs (run_id);

create table run_artifacts (
    artifact_id  uuid        primary key,
    run_id       uuid        not null references runs (run_id),
    kind         text        not null,
    blob_sha256  char(64)    not null references raw_blobs (sha256),
    created_by   text        not null,
    created_at   timestamptz not null
);

create index run_artifacts_lookup_idx on run_artifacts (run_id, kind, created_at);

create trigger run_artifacts_append_only before update or delete on run_artifacts
    for each row execute function calipa_forbid_mutation();
create trigger run_artifacts_no_truncate before truncate on run_artifacts
    for each statement execute function calipa_forbid_mutation();
