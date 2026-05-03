do $$
begin
  create type public.discord_alert_status as enum ('pending', 'sent', 'failed');
exception
  when duplicate_object then null;
end $$;

create table if not exists public.discord_alerts (
  id uuid primary key default gen_random_uuid(),
  news_item_id uuid not null references public.news_items(id) on delete cascade,
  status public.discord_alert_status not null default 'pending',
  payload jsonb not null,
  attempts int not null default 0 check (attempts >= 0),
  last_error text,
  last_status_code int,
  next_attempt_at timestamptz not null default now(),
  sent_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (news_item_id)
);

create index if not exists discord_alerts_pending_idx
  on public.discord_alerts (status, next_attempt_at, created_at)
  where status = 'pending';

create index if not exists logs_created_at_idx
  on public.logs (created_at);

create index if not exists ai_requests_created_at_idx
  on public.ai_requests (created_at);

create index if not exists worker_runs_finished_at_idx
  on public.worker_runs (finished_at)
  where status = 'success';

create index if not exists news_items_status_created_at_idx
  on public.news_items (status, created_at);

alter table public.discord_alerts enable row level security;
