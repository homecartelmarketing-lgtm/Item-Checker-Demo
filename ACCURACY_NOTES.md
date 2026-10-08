# Accuracy notes

## v3.1
- Flash only by default: `ENABLE_ESCALATION=0`, no default escalation model.
- `make_labels.py` builds `labels.csv` from the gallery CSV + operator clicks (stable dev/holdout split).
- Caches are keyed on settings and prompt/rule/few-shot text. Editing a prompt or a threshold no longer
  returns stale answers. First run after upgrading is a full (uncached) run.
- `MAX_SIDE` configurable (default 1024, unchanged).
- `ZOOM_CROPS=1` (off by default) sends a top band (canopy + cords) and a middle band (arms + tiers) of both
  photos to describe and compare. Fixture box from FastSAM when `ultralytics` is installed, otherwise a
  white-background box; room scenes without FastSAM fall back to whole-image bands. Critic is unchanged.
- Every result now carries `settings` (max_side, zoom_crops, sig) and the debug log records images per call.

## v3
- Restored the v2 multi-step pipeline (describe, hard rules, two-way critic, escalation, dedup) that the
  "visual-only" rewrite had replaced with a single Qwen call. Logic now lives in `pipeline.py`.
- Prompts moved out of code into `prompts/*.md`. Category rules in `knowledge/rules/*.md` are now actually
  sent to Qwen (before, nothing loaded them).
- Rules rewritten to match the visual-only spec: removed the OCR model-suffix rule and the
  "below 85% = mismatch" rule (low confidence now goes to review), finish is never a hard veto.
- Removed `knowledge/few_shots/chandelier_mismatches.md` (it described Andora, a product in the batch,
  so it leaked answers). Few-shots are now real image pairs in `knowledge/few_shots/pairs.json`,
  automatically skipped for their own product.
- Images are downloaded once, cached, and sent to Qwen as base64 (alicdn hotlink blocks caused errors).
- DINOv2 no longer squashes images to a square (letterbox pad instead) and now feeds a soft conflict.
- Separate thresholds: VALID_CONF 90, INVALID_CONF 85 (was 80/80 hardcoded; .env values were ignored).
- Swapped-order re-check on borderline answers to catch position bias.
- Per-pair result cache: re-runs are fast and cheap; tick "Re-check all" in the UI to bypass.

## v2 background
- Hero photo was compared against itself (free 100% matches); duplicates counted twice.
- API errors were counted as mismatch; there was no review state.

## Before tuning anything
No `labels.csv` exists yet, so every threshold is still a guess. Run `python make_labels.py`, label,
then run `eval.py`.
