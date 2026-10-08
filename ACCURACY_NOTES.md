# Accuracy notes

## v3 (this version)
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
No `labels.csv` exists yet, so every threshold is still a guess. Label first, then run `eval.py`.
