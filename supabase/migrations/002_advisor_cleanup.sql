create index if not exists ai_requests_news_item_id_idx
  on public.ai_requests (news_item_id);

create index if not exists posts_draft_id_idx
  on public.posts (draft_id);
