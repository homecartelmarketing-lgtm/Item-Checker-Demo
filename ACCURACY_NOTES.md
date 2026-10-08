# Accuracy Upgrade v2

## Why
The v1 numbers looked better than reality:
- Hero photo was compared against itself in most products (free 100% matches).
- Duplicate gallery rows were checked and counted twice.
- Andora's answers were in the few-shot prompt (data leakage).
- The CV pipeline squashed images to squares, which hides rectangular vs round, and approved almost everything.
- API errors were counted as "mismatch". There was no "Needs Review".

## New flow (qwen_server.py)
1. **Overrides / duplicates**: operator overrides win. Same image as hero (URL or dHash) is valid with no AI call. Duplicate rows reuse the first result.
2. **Describe**: Qwen reads each image alone (image type, fixture type, shape, cords, tiers, heads, finish). Cached in `attr_cache.json`.
3. **Compare in code**: hard rules decide clear mismatches (round vs rectangular, cord count, sconce vs chandelier, single vs multi-head pendant).
4. **Side-by-side + critic**: holistic compare at temperature 0. Critic runs on matches (catch false passes) and on unsupported mismatches (catch false flags).
5. **Escalate**: borderline cases go to `QWEN_ESCALATION_MODEL`.
6. **Verdict**: `valid` / `invalid` / `review` / `error`.

`status` is still `match`/`mismatch` so the current UI works. Review and error items show as flagged, with `NEEDS REVIEW:` / `ERROR (retry):` in the reason.

## CV pipeline (auto_checker.py)
Letterbox resize, raw DINOv2 cosine, RANSAC inliers, background removal on both sides, writes `cv_results.json` (the Qwen server reads it as extra evidence). It no longer overwrites `ai_results.json`.

## Measure before tuning
1. Copy `labels_template.csv` to `labels.csv` and label 200-300 photos (60+ invalid). Mark 30% as `holdout`.
2. Run the checker, then: `python eval.py --labels labels.csv --results ai_results.json`
3. Tune `VALID_CONF`, `INVALID_CONF`, `ATTR_CONF` in `.env` on `dev` only, confirm on `--split holdout`.
4. Targets: missed bad photos about 0, invalid caught >= 95%, review rate <= 20%.

## Debugging
Every Qwen request, raw response, parse failure and retry is logged to `logs/verify_debug.jsonl`.
