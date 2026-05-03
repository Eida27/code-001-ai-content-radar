create unique index if not exists drafts_news_item_id_draft_type_unique_idx
  on public.drafts (news_item_id, draft_type);
