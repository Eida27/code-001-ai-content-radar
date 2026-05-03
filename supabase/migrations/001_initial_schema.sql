create extension if not exists pgcrypto;

do $$
begin
  create type public.source_type as enum ('rss', 'html', 'github', 'arxiv', 'x_api');
exception
  when duplicate_object then null;
end $$;

do $$
begin
  create type public.news_item_status as enum (
    'new',
    'low_priority',
    'scored',
    'drafted',
    'needs_review',
    'approved',
    'posted',
    'rejected',
    'archived',
    'draft_failed',
    'error'
  );
exception
  when duplicate_object then null;
end $$;

do $$
begin
  create type public.draft_status as enum ('needs_review', 'approved', 'posted', 'rejected', 'archived');
exception
  when duplicate_object then null;
end $$;

do $$
begin
  create type public.draft_type as enum ('short_post', 'long_post', 'thread', 'why_it_matters', 'risk_note');
exception
  when duplicate_object then null;
end $$;

do $$
begin
  create type public.worker_run_status as enum ('running', 'success', 'error');
exception
  when duplicate_object then null;
end $$;

do $$
begin
  create type public.log_level as enum ('debug', 'info', 'warning', 'error');
exception
  when duplicate_object then null;
end $$;

do $$
begin
  create type public.ai_request_status as enum ('success', 'parse_error', 'rate_limited', 'error');
exception
  when duplicate_object then null;
end $$;

create table if not exists public.sources (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  type public.source_type not null,
  url text not null,
  category text,
  priority int not null default 5 check (priority between 0 and 10),
  is_active boolean not null default true,
  last_checked_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.news_items (
  id uuid primary key default gen_random_uuid(),
  source_id uuid references public.sources(id) on delete set null,
  title text not null,
  url text not null,
  canonical_url text,
  normalized_url_hash text not null,
  canonical_url_hash text,
  content_hash text not null,
  raw_summary text,
  source_name text,
  published_at timestamptz,
  status public.news_item_status not null default 'new',
  importance_score int check (importance_score is null or importance_score between 0 and 10),
  importance_reason text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (normalized_url_hash),
  unique (canonical_url_hash),
  unique (content_hash)
);

create table if not exists public.drafts (
  id uuid primary key default gen_random_uuid(),
  news_item_id uuid not null references public.news_items(id) on delete cascade,
  draft_type public.draft_type not null,
  content text not null,
  model_used text,
  status public.draft_status not null default 'needs_review',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.posts (
  id uuid primary key default gen_random_uuid(),
  draft_id uuid references public.drafts(id) on delete set null,
  platform text not null default 'x',
  posted_url text,
  posted_at timestamptz,
  posting_method text not null check (posting_method in ('manual', 'scheduled', 'x_api')),
  created_at timestamptz not null default now()
);

create table if not exists public.worker_runs (
  id uuid primary key default gen_random_uuid(),
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  status public.worker_run_status not null default 'running',
  sources_checked int not null default 0,
  items_found int not null default 0,
  items_inserted int not null default 0,
  drafts_created int not null default 0,
  errors_count int not null default 0,
  metadata jsonb not null default '{}'::jsonb
);

create table if not exists public.logs (
  id uuid primary key default gen_random_uuid(),
  level public.log_level not null,
  module text not null,
  message text not null,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists public.ai_requests (
  id uuid primary key default gen_random_uuid(),
  news_item_id uuid references public.news_items(id) on delete set null,
  model_used text not null,
  status public.ai_request_status not null,
  raw_response text,
  parse_error text,
  created_at timestamptz not null default now()
);

create index if not exists sources_active_idx
  on public.sources (priority desc, name)
  where is_active = true;

create index if not exists news_items_source_id_idx
  on public.news_items (source_id);

create index if not exists news_items_published_at_idx
  on public.news_items (published_at desc);

create index if not exists news_items_status_idx
  on public.news_items (status);

create index if not exists news_items_created_at_idx
  on public.news_items (created_at desc);

create index if not exists drafts_news_item_id_idx
  on public.drafts (news_item_id);

create index if not exists worker_runs_status_idx
  on public.worker_runs (status, started_at desc);

create index if not exists ai_requests_model_created_at_idx
  on public.ai_requests (model_used, created_at desc);

alter table public.sources enable row level security;
alter table public.news_items enable row level security;
alter table public.drafts enable row level security;
alter table public.posts enable row level security;
alter table public.worker_runs enable row level security;
alter table public.logs enable row level security;
alter table public.ai_requests enable row level security;

insert into public.sources (name, type, url, category, priority)
values
  ('OpenAI News', 'rss', 'https://openai.com/news/rss.xml', 'official_ai_company', 10),
  ('Hugging Face Blog', 'rss', 'https://huggingface.co/blog/feed.xml', 'ai_models', 8),
  ('GitHub Blog AI', 'rss', 'https://github.blog/feed/', 'developer_ai', 7)
on conflict do nothing;
