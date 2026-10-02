-- Run in a disposable Supabase test database using pgTAP, not against live channel data.
begin;
select plan(7);
select ok((select relrowsecurity from pg_class where oid='public.channels'::regclass),'channels enforce RLS');
select ok((select relrowsecurity from pg_class where oid='public.workers'::regclass),'workers enforce RLS');
select ok((select relrowsecurity from pg_class where oid='public.control_jobs'::regclass),'jobs enforce RLS');
select ok(not has_table_privilege('anon','public.control_jobs','select,insert,update,delete'),'anonymous users cannot access jobs');
select ok(not has_column_privilege('authenticated','public.workers','token_hash','select'),'worker hashes are not browser readable');
select ok(not has_function_privilege('authenticated','public.claim_cliprank_job(uuid)','execute'),'browser cannot claim worker jobs');
select ok(not has_function_privilege('anon','public.pair_cliprank_worker(uuid,text)','execute'),'anonymous users cannot pair workers');
select * from finish();
rollback;
