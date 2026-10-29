-- Anchors: the audit trail's hash chains, copied out of the database.
--
-- Each run's events form a hash chain, so editing an event breaks every later hash.
-- Someone with full database access could still rewrite a whole chain and its
-- hashes. An anchor records the latest (seq, row_hash) of every run that changed
-- since the previous anchor; anchors are themselves hash-chained, and each one is
-- also written to storage the database cannot change (write-once, outside Calipa's
-- control). A rewritten chain then no longer matches what was published.

create table chain_anchors (
    seq          integer     primary key check (seq >= 1),
    created_at   timestamptz not null,
    heads        jsonb       not null,  -- [{run_id, seq, row_hash}], by run_id
    prev_hash    char(64),
    anchor_hash  char(64)    not null unique,
    constraint chain_anchors_first_has_no_prev check ((seq = 1) = (prev_hash is null))
);

-- Where each anchor was published. Written after the store accepted it; an anchor
-- without a receipt is published on the next pass.
create table anchor_receipts (
    anchor_seq    integer     not null references chain_anchors (seq),
    sink          text        not null,
    receipt       text        not null,
    published_at  timestamptz not null,
    primary key (anchor_seq, sink)
);

do $$
declare
    t text;
begin
    foreach t in array array['chain_anchors', 'anchor_receipts']
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
