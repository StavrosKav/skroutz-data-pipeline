## Conclusions (2026-10-28, historical, 20-question set)

> Historical. Written for the original 20 questions; the set now has 23.

Caveat first: 20 questions, one run per cell (two for Ultra v1). A 1-2 question
gap is noise, not a ranking. Treat this as a smoke-level comparison.

1. **Ultra + v1 stays the production choice.** 18/20 on both runs, the same two
   questions failed both times, so run-to-run variance at temperature 0 was zero on
   this set. Median ~29 s per question, p90 ~41-54 s.
2. **gpt-oss-20b matches Ultra on accuracy (18/20, same two failures)** with fewer
   tokens, but its tail is unusable for a chat bot: p90 130 s. Kept as last fallback.
3. **Bolt is ~8x faster (median 3.7 s) but 13/20.** All 7 failures are wrong
   results from valid SQL (missing "still listed"/date filters, wrong window,
   AVG instead of index table). Good fallback for availability, not for accuracy.
4. **The v1 hints help Ultra (+2 vs v0).** Without them it fails
   laptop_most_expensive (NULL prices first) and apple_median_tablet (AVG for
   median) - exactly what the hints target.
5. **Few-shot (v2) did not help: 17/20,** costs ~45% more prompt tokens, and swapped
   failures (one invalid SQL, median and all-time-low regress). Not adopted.
6. **The two questions every strong model fails** point at the eval as much as
   at the models: `biggest_drop_pct_laptop_week` ("this week" = `> max-7` in the
   reference, models use `>= max - 7 days`) and `price_history_top_phone` (model
   does not restrict to currently listed products). Next step: tighten these
   question wordings/notes and grow the set to ~50 questions before drawing
   finer conclusions.

Tokens are from the API `usage` field and are not comparable across models with
different tokenizers (Bolt counts the same prompt as ~1.7x more tokens). All
models are on the NVIDIA free tier, so cost per query is $0 at current usage.

Latency note: the runs went in three parallel streams against the same API key, so absolute seconds include some queueing; compare them relative to each other.
