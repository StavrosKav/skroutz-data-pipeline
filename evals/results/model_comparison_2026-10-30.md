# /ask model comparison, 2026-10-30

Same 23 questions (`evals/ask_eval.yaml`), same checker, reference answers computed live from the database.
Each run pins one NVIDIA NIM model with fallback off (`python evals/run_ask_eval.py --model <name>`),
so every score belongs to that model only. Runs were done on 2026-10-30 between 20:43 and 22:30 (Athens time).

## Results

| Model | Complete runs | Score per run | Mean | Spread | Median latency / question | p90 latency / question | Errors (timeouts etc.) | Wall time per run |
|---|---|---|---|---|---|---|---|---|
| nvidia/nemotron-3-ultra-550b-a55b | 1 | 22/23 | 22.0 | n/a (1 run) | 25.5 s | 58.0 s | 1 (ReadTimeout) | 13 min 43 s |
| openai/gpt-oss-20b | 2 | 21/23, 17/23 | 19.0 | 4 | 26.2 s | 134.5 s | 6 (5 ReadTimeout, 1 SQL error) | 16 min 47 s, 44 min 0 s |
| nvidia/nemotron-3.5-bolt-e4b | 1 | 13/23 | 13.0 | n/a (1 run) | 3.6 s | 5.0 s | 0 | 1 min 34 s |

Latency is the agent's total time per question (model call plus SQL), pooled over the complete runs.
The pass target is 19/23 (80%).

## What went wrong during the runs (reported as is)

- **Nemotron 3 Ultra:** two more runs were started. Both stopped with `429 Too Many Requests` from NIM (free tier rate limit),
  once at 21:15 and again on retries at 22:23 and 22:26 after a 2 minute pause. These runs are not counted.
  Its one miss in the complete run (`new_products_week`) was a timeout, not a wrong answer.
- **gpt-oss-20b:** run 2 was slow on NIM's side: 5 questions hit a read timeout and 4 of those count as fails.
  So the 21 vs 17 spread is mostly the service, not the model's SQL. The answer that came back
  for `falling_trend_count` was wrong in both runs.
- **Nemotron 3.5 Bolt:** fast, but wrong on 10 of 23. Its misses are logic errors
  (wrong filter for "current" items, wrong averages), not timeouts. A second run failed at 21:23 and at 22:21
  with `404 Not Found`, so the model was not reachable on this account at that time. It is recorded as unavailable for run 2.
- The eval script stops on an uncaught NIM error (429 or 404), so a crashed run leaves no partial score.

## Takeaway

Ultra gives the most correct answers. gpt-oss-20b is close when NIM is healthy, but slower in the tail.
Bolt answers in about 4 s but fails the 80% target, so it stays a fallback only.
More runs per model are needed to measure the real spread; this table shows only what was measured on 2026-10-30.
