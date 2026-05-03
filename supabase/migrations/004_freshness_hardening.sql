alter table public.sources
  add column if not exists last_success_at timestamptz,
  add column if not exists last_error_at timestamptz,
  add column if not exists last_error_message text,
  add column if not exists latest_feed_published_at timestamptz,
  add column if not exists latest_stored_published_at timestamptz;

create index if not exists sources_latest_feed_published_at_idx
  on public.sources (latest_feed_published_at desc)
  where is_active = true;

create index if not exists sources_latest_stored_published_at_idx
  on public.sources (latest_stored_published_at desc)
  where is_active = true;

insert into public.sources (name, type, url, category, priority)
select 'Google AI Blog', 'rss', 'https://blog.google/innovation-and-ai/technology/ai/rss/', 'official_ai_company', 9
where not exists (
  select 1 from public.sources
  where url = 'https://blog.google/innovation-and-ai/technology/ai/rss/'
);

insert into public.sources (name, type, url, category, priority)
select 'NVIDIA AI Blog', 'rss', 'https://blogs.nvidia.com/blog/category/deep-learning/feed/', 'ai_hardware_models', 8
where not exists (
  select 1 from public.sources
  where url = 'https://blogs.nvidia.com/blog/category/deep-learning/feed/'
);

insert into public.sources (name, type, url, category, priority)
select 'TensorFeed AI', 'rss', 'https://tensorfeed.ai/feed.xml', 'ai_aggregator', 5
where not exists (
  select 1 from public.sources
  where url = 'https://tensorfeed.ai/feed.xml'
);
