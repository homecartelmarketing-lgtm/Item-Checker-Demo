# Item Checker

Checks whether each gallery photo shows the **same physical product variant** as the hero photo
(Image Position 1). Built for Akeneo / Shopify lighting catalogs where supplier galleries mix
round, rectangular, sconce and other variants.

## Run

```bash
pip install -r requirements.txt
cp .env.example .env        # add DASHSCOPE_API_KEY
python qwen_server.py       # or double-click run.bat on Windows
```

Open http://localhost:8089, press **Check all photos**, then work the **Invalid** and **Review** tabs.
Click any photo to see it next to the hero and mark it ✓ Valid / ✗ Invalid (keys `v` / `x`).
Your decisions are saved and always win over the AI. **Download CSV** exports the verdicts.

## How a photo is checked (`pipeline.py`)

1. Operator decision wins.
2. Same image as hero (URL or dHash) is valid without an AI call. Gallery duplicates reuse the first result.
3. Qwen describes each image alone (shape, cords, tiers, heads, fixture type). Cached.
4. Hard rules in code catch clear mismatches: round vs rectangular, cord count, tiers, single vs multi-head, sconce vs ceiling light.
5. Qwen compares hero and photo side by side. Optional image-pair few-shots.
6. A critic double-checks: tries to veto matches, and tries to defend unsupported mismatches.
7. Borderline answers are re-asked with the images swapped, then escalated to a stronger model.
8. Result: `valid` / `invalid` / `review` / `error`. Errors are never counted as invalid.

Only visible appearance is used. Text, model codes, dimensions, titles, URLs and backgrounds are ignored.

## Changing how Qwen thinks

Edit the Markdown in `prompts/` and `knowledge/rules/`. These files are sent to Qwen on every run,
see `prompts/README.md`. Then measure (below). Don't add a rule without a labelled example that needs it.

## Measure accuracy

1. Copy `labels_template.csv` to `labels.csv`, label 200-300 photos (60+ invalid), mark 30% `holdout`.
2. `python eval.py --labels labels.csv --results ai_results.json`
3. Tune thresholds in `.env` on `dev`, confirm once on `--split holdout`.

Targets: missed bad photos about 0, invalid caught at least 95%, review rate at most 20%.

## Files

| File | Purpose |
| --- | --- |
| `qwen_server.py` | Web server + streaming API |
| `pipeline.py` | All checking logic (also a CLI) |
| `visual_only_matcher.py` | Image download/cache, letterbox resize, dHash, optional DINOv2 |
| `prompts/`, `knowledge/rules/` | Text sent to Qwen |
| `knowledge/feedback/overrides.json` | Operator decisions |
| `ai_results.json`, `InvalidSideImages_Verified.csv` | Output |
| `logs/verify_debug.jsonl` | Every Qwen request/response for debugging |
