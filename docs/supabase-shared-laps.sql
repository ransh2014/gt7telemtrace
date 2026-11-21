-- TRACE 1.6.0 -- shared lap links (opt-in).  Supabase project: gt7trace
--
-- Run in the Supabase SQL editor (it was run once on the gt7trace project on 2026-11-21).  Nothing in the TRACE code connects
-- to Supabase to apply it.  It is safe to run twice (everything is IF NOT EXISTS / CREATE OR REPLACE).
--
-- What this adds
--   * table  shared_laps          one row per shared lap; clients can NOT select/insert/update/delete it directly
--   * table  shared_lap_log       one row per share, only used for the per-user daily rate limit
--   * rpc    share_lap(...)       signed-in (anonymous auth) users only; validates, rate-limits, returns the id
--   * rpc    get_shared_lap(id)   anyone with the link; the only way to read a row
--   * rpc    delete_shared_lap(id) the owner only
--   * a pg_cron job that deletes rows once expires_at (created_at + 90 days) has passed
--
-- Links are unlisted: ids are random (80 bits) and there is no list/search function.
--
-- The payload is a gzip of a small JSON document (format v1, see src/gt7telem/sharelink.py), sent base64-encoded.
-- Postgres can't look inside a gzip, so the server checks what it can see: the gzip magic bytes, the size cap,
-- and the sample count / lap time the client declares.  The viewer page (lap.html) re-checks everything after it
-- decompresses and treats all of it as untrusted text.

create extension if not exists pg_cron;

-- ── tables ─────────────────────────────────────────────────────────────────────────────────────────────────────
create table if not exists public.shared_laps (
    id          text primary key,
    owner       uuid not null,
    car         text not null,
    track       text not null,
    lap_time_ms integer not null,
    samples     integer not null,
    payload     bytea not null,
    created_at  timestamptz not null default now(),
    expires_at  timestamptz not null default (now() + interval '90 days'),
    constraint shared_laps_id_shape  check (id ~ '^[0-9a-f]{20}$'),
    constraint shared_laps_size_cap  check (octet_length(payload) <= 204800),
    constraint shared_laps_time_ok   check (lap_time_ms between 10000 and 3600000),
    constraint shared_laps_samples   check (samples between 2 and 20000),
    constraint shared_laps_names     check (char_length(car) between 1 and 120 and char_length(track) between 1 and 120)
);
create index if not exists shared_laps_owner_idx   on public.shared_laps (owner);
create index if not exists shared_laps_expires_idx on public.shared_laps (expires_at);

create table if not exists public.shared_lap_log (
    owner      uuid not null,
    created_at timestamptz not null default now()
);
create index if not exists shared_lap_log_owner_idx on public.shared_lap_log (owner, created_at);

-- Row level security ON with NO policies: the anon and authenticated roles can't touch either table directly.
-- (The SECURITY DEFINER functions below run as the table owner and are the only way in.)
alter table public.shared_laps    enable row level security;
alter table public.shared_lap_log enable row level security;
revoke all on public.shared_laps    from anon, authenticated;
revoke all on public.shared_lap_log from anon, authenticated;

-- ── share_lap ──────────────────────────────────────────────────────────────────────────────────────────────────
-- p_payload is base64 of the gzip bytes.  Returns the new id.
create or replace function public.share_lap(
    p_car         text,
    p_track       text,
    p_lap_time_ms integer,
    p_samples     integer,
    p_payload     text
) returns text
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    v_uid   uuid := auth.uid();
    v_bytes bytea;
    v_id    text;
    v_tries integer := 0;
begin
    if v_uid is null then
        raise exception 'sign-in required' using errcode = '28000';
    end if;

    -- same spirit as validate_lap_submission(): refuse what can't be a real lap
    if p_car is null or p_track is null or char_length(btrim(p_car)) = 0 or char_length(btrim(p_track)) = 0
       or char_length(p_car) > 120 or char_length(p_track) > 120 then
        raise exception 'bad car or track name' using errcode = '22023';
    end if;
    if p_lap_time_ms is null or p_lap_time_ms < 10000 or p_lap_time_ms > 3600000 then
        raise exception 'lap time out of range' using errcode = '22023';
    end if;
    if p_samples is null or p_samples < 2 or p_samples > 20000 then
        raise exception 'sample count out of range' using errcode = '22023';
    end if;
    if p_payload is null or char_length(p_payload) > 290000 then      -- ~200 KB of bytes once base64 is decoded
        raise exception 'payload too large' using errcode = '22023';
    end if;

    begin
        v_bytes := decode(p_payload, 'base64');
    exception when others then
        raise exception 'payload is not base64' using errcode = '22023';
    end;
    if octet_length(v_bytes) > 204800 then
        raise exception 'payload too large' using errcode = '22023';
    end if;
    if octet_length(v_bytes) < 20 or get_byte(v_bytes, 0) <> 31 or get_byte(v_bytes, 1) <> 139 then   -- 1f 8b
        raise exception 'payload is not gzip' using errcode = '22023';
    end if;

    -- rate limit: 20 shares per user per rolling day (deleting a share does not give the allowance back)
    delete from public.shared_lap_log where created_at < now() - interval '2 days';
    if (select count(*) from public.shared_lap_log where owner = v_uid and created_at > now() - interval '1 day') >= 20 then
        raise exception 'rate limit: 20 shared laps per day' using errcode = '54000';
    end if;

    loop
        v_id := left(replace(gen_random_uuid()::text, '-', ''), 10) || left(replace(gen_random_uuid()::text, '-', ''), 10);
        begin
            insert into public.shared_laps (id, owner, car, track, lap_time_ms, samples, payload)
            values (v_id, v_uid, btrim(p_car), btrim(p_track), p_lap_time_ms, p_samples, v_bytes);
            exit;
        exception when unique_violation then
            v_tries := v_tries + 1;
            if v_tries > 5 then raise exception 'could not allocate an id'; end if;
        end;
    end loop;

    insert into public.shared_lap_log (owner) values (v_uid);
    return v_id;
end;
$$;

-- ── get_shared_lap ─────────────────────────────────────────────────────────────────────────────────────────────
create or replace function public.get_shared_lap(p_id text)
returns table (car text, track text, lap_time_ms integer, samples integer, payload text, created_at timestamptz, expires_at timestamptz)
language sql
security definer
stable
set search_path = public, pg_temp
as $$
    select s.car, s.track, s.lap_time_ms, s.samples, encode(s.payload, 'base64'), s.created_at, s.expires_at
    from public.shared_laps s
    where s.id = lower(btrim(p_id)) and s.expires_at > now()
    limit 1;
$$;

-- ── delete_shared_lap ──────────────────────────────────────────────────────────────────────────────────────────
create or replace function public.delete_shared_lap(p_id text)
returns boolean
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    v_n integer;
begin
    if auth.uid() is null then
        raise exception 'sign-in required' using errcode = '28000';
    end if;
    delete from public.shared_laps where id = lower(btrim(p_id)) and owner = auth.uid();
    get diagnostics v_n = row_count;
    return v_n > 0;
end;
$$;

-- ── who may call what ──────────────────────────────────────────────────────────────────────────────────────────
revoke all on function public.share_lap(text, text, integer, integer, text) from public;
revoke all on function public.get_shared_lap(text)                          from public;
revoke all on function public.delete_shared_lap(text)                       from public;
-- Supabase grants new functions to anon by default, so take that away explicitly (share/delete need a signed-in user).
revoke execute on function public.share_lap(text, text, integer, integer, text) from anon;
revoke execute on function public.delete_shared_lap(text)                       from anon;
grant execute on function public.share_lap(text, text, integer, integer, text) to authenticated;
grant execute on function public.get_shared_lap(text)                          to anon, authenticated;
grant execute on function public.delete_shared_lap(text)                       to authenticated;

-- ── 90-day clean-up ────────────────────────────────────────────────────────────────────────────────────────────
-- expires_at is created_at + 90 days.  Runs daily at 03:17 UTC.  (get_shared_lap already hides expired rows.)
select cron.unschedule('delete-expired-shared-laps')
where exists (select 1 from cron.job where jobname = 'delete-expired-shared-laps');
select cron.schedule('delete-expired-shared-laps', '17 3 * * *',
                     $$delete from public.shared_laps where expires_at < now()$$);
