-- Manual allowlist examples for RSS-compatible unofficial sources.
-- Replace the example names and URLs, then set approved = true for rows you
-- have reviewed. Running this file unchanged inserts nothing.

with candidate_sources(name, type, url, category, priority, approved) as (
  values
    (
      'Example Verified Creator',
      'rss',
      'https://creator.example/feed.xml',
      'verified_creator',
      7,
      false
    ),
    (
      'Example Reliable Forum',
      'rss',
      'https://forum.example/rss',
      'reliable_forum',
      6,
      false
    ),
    (
      'Example AI Aggregator',
      'rss',
      'https://aggregator.example/feed.xml',
      'ai_aggregator',
      5,
      false
    )
)
insert into public.sources (name, type, url, category, priority)
select name, type::public.source_type, url, category, priority
from candidate_sources candidate
where not exists (
    select 1
    from public.sources existing
    where existing.url = candidate.url
  )
  and approved = true;
