create table if not exists public.worker_locks (
  lock_name text primary key,
  holder text not null,
  acquired_at timestamptz not null default now(),
  expires_at timestamptz not null,
  metadata jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);

create index if not exists worker_locks_expires_at_idx
  on public.worker_locks (expires_at);

alter table public.worker_locks enable row level security;

revoke all on table public.worker_locks from anon, authenticated;
grant all on table public.worker_locks to service_role;

create or replace function public.try_acquire_worker_lock(
  p_lock_name text,
  p_holder text,
  p_ttl_seconds int
)
returns boolean
language plpgsql
security invoker
set search_path = public
as $$
declare
  v_acquired boolean;
begin
  insert into public.worker_locks (
    lock_name,
    holder,
    acquired_at,
    expires_at,
    updated_at
  )
  values (
    p_lock_name,
    p_holder,
    now(),
    now() + make_interval(secs => greatest(p_ttl_seconds, 1)),
    now()
  )
  on conflict (lock_name) do update
    set holder = excluded.holder,
        acquired_at = excluded.acquired_at,
        expires_at = excluded.expires_at,
        updated_at = excluded.updated_at
    where public.worker_locks.expires_at <= now()
       or public.worker_locks.holder = excluded.holder
  returning true into v_acquired;

  return coalesce(v_acquired, false);
end;
$$;

create or replace function public.release_worker_lock(
  p_lock_name text,
  p_holder text
)
returns boolean
language plpgsql
security invoker
set search_path = public
as $$
declare
  v_released boolean;
begin
  delete from public.worker_locks
  where lock_name = p_lock_name
    and holder = p_holder
  returning true into v_released;

  return coalesce(v_released, false);
end;
$$;

create or replace function public.production_readiness_snapshot()
returns jsonb
language sql
security invoker
set search_path = public, storage
as $$
  with required_tables(table_name) as (
    values
      ('sources'),
      ('news_items'),
      ('drafts'),
      ('posts'),
      ('worker_runs'),
      ('logs'),
      ('ai_requests'),
      ('discord_alerts'),
      ('worker_locks')
  ),
  table_counts as (
    select
      required_tables.table_name,
      coalesce(stats.n_live_tup, 0)::bigint as row_count
    from required_tables
    left join pg_stat_user_tables stats
      on stats.schemaname = 'public'
     and stats.relname = required_tables.table_name
  ),
  rls_status as (
    select
      required_tables.table_name,
      coalesce(classes.relrowsecurity, false) as rls_enabled
    from required_tables
    left join pg_class classes
      on classes.relname = required_tables.table_name
    left join pg_namespace namespaces
      on namespaces.oid = classes.relnamespace
     and namespaces.nspname = 'public'
  ),
  duplicate_drafts as (
    select count(*)::bigint as groups
    from (
      select news_item_id, draft_type
      from public.drafts
      group by news_item_id, draft_type
      having count(*) > 1
    ) duplicates
  ),
  duplicate_news_hashes as (
    select count(*)::bigint as groups
    from (
      select normalized_url_hash
      from public.news_items
      where normalized_url_hash is not null
      group by normalized_url_hash
      having count(*) > 1
    ) duplicates
  ),
  storage_usage as (
    select
      count(*)::bigint as object_count,
      coalesce(sum(nullif(metadata->>'size', '')::bigint), 0)::bigint as total_bytes
    from storage.objects
  )
  select jsonb_build_object(
    'db_size_bytes', pg_database_size(current_database()),
    'storage_bytes', (select total_bytes from storage_usage),
    'storage_objects', (select object_count from storage_usage),
    'table_counts', (
      select jsonb_object_agg(table_name, row_count)
      from table_counts
    ),
    'rls', (
      select jsonb_object_agg(table_name, rls_enabled)
      from rls_status
    ),
    'index_names', (
      select coalesce(jsonb_agg(indexname order by indexname), '[]'::jsonb)
      from pg_indexes
      where schemaname = 'public'
    ),
    'function_names', (
      select coalesce(jsonb_agg(distinct procedures.proname), '[]'::jsonb)
      from pg_proc procedures
      join pg_namespace namespaces
        on namespaces.oid = procedures.pronamespace
      where namespaces.nspname = 'public'
        and procedures.proname in (
          'try_acquire_worker_lock',
          'release_worker_lock',
          'production_readiness_snapshot'
        )
    ),
    'duplicate_draft_groups', (select groups from duplicate_drafts),
    'duplicate_news_hash_groups', (select groups from duplicate_news_hashes),
    'running_worker_runs', (
      select count(*)::bigint
      from public.worker_runs
      where status = 'running'
    ),
    'pending_discord_alerts', (
      select count(*)::bigint
      from public.discord_alerts
      where status = 'pending'
    )
  );
$$;

revoke execute on function public.try_acquire_worker_lock(text, text, int) from public, anon, authenticated;
revoke execute on function public.release_worker_lock(text, text) from public, anon, authenticated;
revoke execute on function public.production_readiness_snapshot() from public, anon, authenticated;

grant execute on function public.try_acquire_worker_lock(text, text, int) to service_role;
grant execute on function public.release_worker_lock(text, text) to service_role;
grant execute on function public.production_readiness_snapshot() to service_role;
