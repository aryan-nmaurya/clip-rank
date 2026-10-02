begin;
create table public.channels (
  owner_id uuid primary key references auth.users(id) on delete cascade,
  profile jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);
create table public.workers (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references auth.users(id) on delete cascade,
  token_hash text unique not null check(length(token_hash)=64),
  revoked boolean not null default false,
  last_seen timestamptz,
  summary jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);
create table public.control_jobs (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references auth.users(id) on delete cascade,
  day date not null,
  kind text not null check(kind='PLAN_DAY'),
  payload jsonb not null default '{}'::jsonb,
  status text not null default 'CREATED' check(status in ('CREATED','PROCESSING','COMPLETE','FAILED')),
  worker_id uuid references public.workers(id),
  lease_until timestamptz,
  attempts integer not null default 0 check(attempts between 0 and 3),
  created_at timestamptz not null default now(),
  unique(owner_id,day,kind)
);
create index control_jobs_claim on public.control_jobs(owner_id,status,created_at);
alter table public.channels enable row level security;
alter table public.workers enable row level security;
alter table public.control_jobs enable row level security;
revoke all on public.channels, public.workers, public.control_jobs from anon,authenticated;
grant select on public.channels, public.control_jobs to authenticated;
-- Worker token hashes are not exposed to browser clients, including their owner.
grant select(id,owner_id,revoked,last_seen,summary,created_at) on public.workers to authenticated;
grant all on public.channels, public.workers, public.control_jobs to service_role;
create policy channel_owner_read on public.channels for select to authenticated using(auth.uid()=owner_id);
create policy worker_owner_read on public.workers for select to authenticated using(auth.uid()=owner_id);
create policy job_owner_read on public.control_jobs for select to authenticated using(auth.uid()=owner_id);

create function public.pair_cliprank_worker(p_owner uuid,p_token_hash text) returns uuid
language plpgsql security definer set search_path=public,pg_temp as $$
declare identifier uuid;
begin
  -- Serialize token replacement for a channel and atomically revoke previous machines.
  perform pg_advisory_xact_lock(hashtext(p_owner::text));
  update public.workers set revoked=true where owner_id=p_owner;
  insert into public.workers(owner_id,token_hash) values(p_owner,p_token_hash) returning id into identifier;
  return identifier;
end $$;
create function public.claim_cliprank_job(p_worker uuid) returns setof public.control_jobs
language plpgsql security definer set search_path=public,pg_temp as $$
declare selected uuid; person uuid;
begin
  select owner_id into person from public.workers where id=p_worker and not revoked;
  if person is null then raise exception 'Unpaired worker'; end if;
  select id into selected from public.control_jobs
    where owner_id=person and attempts<3 and (status='CREATED' or (status='PROCESSING' and lease_until<now()))
    order by created_at for update skip locked limit 1;
  if selected is null then return; end if;
  return query update public.control_jobs set status='PROCESSING',worker_id=p_worker,lease_until=now()+interval '2 minutes',attempts=attempts+1
    where id=selected returning *;
end $$;
revoke all on function public.pair_cliprank_worker(uuid,text),public.claim_cliprank_job(uuid) from public,anon,authenticated;
grant execute on function public.pair_cliprank_worker(uuid,text),public.claim_cliprank_job(uuid) to service_role;
commit;
