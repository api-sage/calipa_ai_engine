-- Report versions and second-person review.
--
-- A run produces at most one report. Re-investigation creates a new linked run, so
-- the report versions of one audit are the runs in one parent chain: version 1 is
-- the root run, version 2 its child, and so on. A report gets at most one review
-- decision, which is final: an approved version can never change, and changing
-- anything means a new run and a new version.
--
-- The rules that matter most are checked here as well as in code: the reviewer is
-- never the run's initiator, and a decision always names the exact report hash the
-- reviewer saw.

create table reports (
    run_id           uuid        primary key references runs (run_id),
    root_run_id      uuid        not null references runs (run_id),
    version          integer     not null check (version >= 1),
    artifact_id      uuid        not null references run_artifacts (artifact_id),
    report_sha256    char(64)    not null,
    markdown_sha256  char(64)    not null references raw_blobs (sha256),
    flagged          boolean     not null,
    created_at       timestamptz not null,
    unique (root_run_id, version)
);

create table reviews (
    review_id           uuid        primary key,
    run_id              uuid        not null unique references reports (run_id),
    report_sha256       char(64)    not null,
    decision            text        not null
                            check (decision in ('approved', 'rejected', 'reinvestigate')),
    reviewer            text        not null,
    note                text        not null,
    flags_acknowledged  boolean     not null,
    child_run_id        uuid        references runs (run_id),
    created_at          timestamptz not null,
    constraint reviews_child_iff_reinvestigate
        check ((decision = 'reinvestigate') = (child_run_id is not null)),
    constraint reviews_note_unless_approved
        check (decision = 'approved' or length(trim(note)) > 0)
);

create index reports_root_idx on reports (root_run_id, version);

create function calipa_check_review() returns trigger
language plpgsql as $$
declare
    initiator text;
    expected  char(64);
    is_flagged boolean;
begin
    select r.initiated_by into initiator from runs r where r.run_id = new.run_id;
    if new.reviewer = initiator then
        raise exception 'calipa: the reviewer cannot be the person who started the run'
            using errcode = 'check_violation';
    end if;
    select p.report_sha256, p.flagged into expected, is_flagged
        from reports p where p.run_id = new.run_id;
    if new.report_sha256 <> expected then
        raise exception 'calipa: the review names a report hash that is not this run''s report'
            using errcode = 'check_violation';
    end if;
    if new.decision = 'approved' and is_flagged and not new.flags_acknowledged then
        raise exception 'calipa: a flagged report can be approved only with its flags acknowledged'
            using errcode = 'check_violation';
    end if;
    return new;
end;
$$;

create trigger reviews_check before insert on reviews
    for each row execute function calipa_check_review();

do $$
declare
    t text;
begin
    foreach t in array array['reports', 'reviews']
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
