select
  count(*)::bigint as object_count,
  coalesce(sum((metadata->>'size')::bigint), 0)::bigint as total_bytes,
  pg_size_pretty(coalesce(sum((metadata->>'size')::bigint), 0)::bigint) as total_size
from storage.objects;
