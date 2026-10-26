-- Calipa audit trail: foundation tables.
-- Every table here is append-only. Updates, deletes and truncates are rejected by
-- triggers; production also runs the app under a role with INSERT/SELECT only
-- (see ops/db_roles.sql).

create function calipa_forbid_mutation() returns trigger
language plpgsql as $$
begin
    raise exception 'calipa: % on % is not allowed; the audit trail is append-only',
        tg_op, tg_table_name
        using errcode = 'insufficient_privilege';
end;
$$;

-- Content-addressed store for raw bytes: API response bodies, LLM requests and
-- responses. The database itself checks that the key is the SHA-256 of the content.
create table raw_blobs (
    sha256        char(64)    primary key,
    content       bytea       not null,
    content_type  text        not null,
    size_bytes    integer     not null,
    created_at    timestamptz not null default now(),
    constraint raw_blobs_hash_matches check (sha256 = encode(sha256(content), 'hex')),
    constraint raw_blobs_size_matches check (size_bytes = octet_length(content))
);

create table runs (
    run_id           uuid        primary key,
    idempotency_key  text        not null unique,
    initiated_by     text        not null,
    request_text     text        not null,
    created_at       timestamptz not null
);

-- One hash chain per run. row_hash = SHA-256 of the canonical JSON of the row's
-- other columns plus prev_hash, so editing or removing any earlier event breaks
-- every later hash.
create table run_events (
    run_id      uuid        not null references runs (run_id),
    seq         integer     not null check (seq >= 1),
    event_type  text        not null,
    state       text,
    payload     jsonb       not null,
    actor       text        not null,
    created_at  timestamptz not null,
    prev_hash   char(64),
    row_hash    char(64)    not null unique,
    primary key (run_id, seq),
    constraint run_events_first_has_no_prev check ((seq = 1) = (prev_hash is null))
);

create table api_calls (
    call_id         uuid        primary key,
    run_id          uuid        references runs (run_id),
    method          text        not null check (method = 'GET'),
    url             text        not null,
    status_code     integer,
    response_blob   char(64)    references raw_blobs (sha256),
    error           text,
    started_at      timestamptz not null,
    duration_ms     integer     not null check (duration_ms >= 0)
);

-- An LLM call is two rows: the request is written before the call is made, the
-- result after. A request with no result means the process died mid-call.
create table llm_calls (
    call_id          uuid        primary key,
    run_id           uuid        references runs (run_id),
    step             text        not null,
    prompt_name      text        not null,
    prompt_version   text        not null,
    prompt_sha256    char(64)    not null,
    schema_name      text        not null,
    schema_sha256    char(64)    not null,
    model_requested  text        not null,
    effort           text        not null,
    request_blob     char(64)    not null references raw_blobs (sha256),
    started_at       timestamptz not null
);

create table llm_call_results (
    call_id                      uuid        primary key references llm_calls (call_id),
    outcome                      text        not null check (outcome in
                                     ('ok', 'refusal', 'max_tokens', 'empty',
                                      'schema_invalid', 'api_error', 'connection_error')),
    model_served                 text,
    request_id                   text,
    http_status                  integer,
    stop_reason                  text,
    refusal_category             text,
    response_blob                char(64)    references raw_blobs (sha256),
    input_tokens                 integer,
    output_tokens                integer,
    cache_read_input_tokens      integer,
    cache_creation_input_tokens  integer,
    cost_usd                     numeric(14, 6),
    error                        text,
    finished_at                  timestamptz not null,
    duration_ms                  integer     not null check (duration_ms >= 0)
);

create index run_events_type_idx on run_events (event_type);
create index api_calls_run_idx on api_calls (run_id);
create index llm_calls_run_idx on llm_calls (run_id);

do $$
declare
    t text;
begin
    foreach t in array array['raw_blobs', 'runs', 'run_events', 'api_calls',
                             'llm_calls', 'llm_call_results']
    loop
        execute format(
            'create trigger %I before update or delete on %I
             for each row execute function calipa_forbid_mutation()',
            t || '_append_only', t);
        execute format(
            'create trigger %I before truncate on %I
             for each statement execute function calipa_forbid_mutation()',
            t || '_no_truncate', t);
    end loop;
end;
$$;
