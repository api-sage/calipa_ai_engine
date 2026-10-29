-- Daily copies of the bank's FX rate table. The API keeps little or no rate history
-- and GL entries don't say which rate was applied, so past conversions can only be
-- checked against rates Calipa saved itself.

create table fx_rate_snapshots (
    snapshot_id    uuid        primary key,
    api_call_id    uuid        not null references api_calls (call_id),
    response_blob  char(64)    not null references raw_blobs (sha256),
    rate_count     integer     not null check (rate_count >= 0),
    fetched_at     timestamptz not null
);

create table fx_rates (
    snapshot_id        uuid        not null references fx_rate_snapshots (snapshot_id),
    source_rate_id     bigint      not null,
    from_currency      text        not null,
    to_currency        text        not null,
    rate               numeric     not null check (rate > 0),
    rate_date          date        not null,
    source_created_at  timestamptz not null,
    primary key (snapshot_id, source_rate_id)
);

create index fx_rates_lookup_idx on fx_rates (from_currency, to_currency, rate_date);

do $$
declare
    t text;
begin
    foreach t in array array['fx_rate_snapshots', 'fx_rates']
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
