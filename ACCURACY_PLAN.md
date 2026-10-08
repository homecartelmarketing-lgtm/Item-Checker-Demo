# Accuracy Plan (Qwen 3.8 Flash only)

Goal: catch every gallery photo that shows a different variant than the hero, without using any
other Qwen model. Local tools (DINOv2, background removal, FastSAM) are allowed because they are not Qwen.

Targets (measured with `eval.py` on the holdout split):

| Metric | Target |
| --- | --- |
| Missed bad photos (invalid auto-passed as valid) | about 0 |
| Invalid caught (flagged or sent to review) | 95% or more |
| False flags (valid auto-flagged invalid) | 5% or less |
| Review rate | 20% or less |

Rule for every phase: **measure before and after**. If a change does not improve the numbers, remove it.

---

## Phase 0: Flash only + measurement (do first)

- [ ] Turn off escalation: `ENABLE_ESCALATION=0` default, remove `qwen-vl-max` references.
- [ ] `make_labels.py`: convert operator clicks in `knowledge/feedback/overrides.json` into `labels.csv`
      (handle, url, label, category, image_type, split). Auto-assign 30% to `holdout`, stable by URL hash.
- [ ] Label at least 200 to 300 photos (60+ invalid) through the UI.
- [ ] Run baseline: `python eval.py --labels labels.csv --results ai_results.json --split holdout`. Save the numbers below.

Baseline: _fill in_

## Phase 1: Better input (biggest gain)

The model can only judge what it can see. Most errors come from tiny cords, busy rooms and one weak reference.

- [ ] **Background removal on BOTH hero and candidate** (rembg). Never only one side.
- [ ] **Send cutout AND original** to Qwen. Cutouts can erase transparent crystals and thin suspension cords,
      and cord count is the strongest variant signal, so the original must always be included.
- [ ] **Fixture crop for room scenes** with FastSAM (`FastSAM-s.pt` already in repo): crop to the fixture
      before comparing instead of removing the background of the whole room.
- [ ] **Zoom crops**: from the fixture box, crop the top band (canopy + cords) and the middle band (arms + tiers)
      and send them as extra images. Fixes cord / arm miscounts.
- [ ] **Higher resolution**: make `MAX_SIDE` configurable (now 1024). Test 1280 / 1536 if flash accepts it.
- [ ] **Multiple references**: after a photo is confirmed valid (operator click, or valid with confidence 95+),
      add it as an extra reference for the rest of that product. Compare candidates against hero + up to 2 references.

## Phase 2: Strict mode (every photo through every phase)

`STRICT_MODE=1` in `.env`:

- [ ] Critic runs on every photo (veto on matches, defend on mismatches), not only borderline ones.
- [ ] Swapped-order recheck on every photo, not only below `SWAP_BELOW`.
- [ ] Keep only two safe shortcuts: exact copy of hero (same file) and operator decisions.
- [ ] Log API calls and seconds per photo so the cost of strict mode is visible.

Expected cost: about 2 to 3 times more flash calls per photo.

## Phase 3: Make flash more reliable (same model, smarter use)

- [ ] **Counting-first prompt**: Qwen must output counts before the verdict
      (`suspension_count`, `arm_count`, `tier_count`, `head_count`, `shape`) for BOTH images, then decide.
      Code compares the counts; the model's verdict is only one input.
- [ ] **Voting**: on review-zone photos run compare 3 times (slight temperature, e.g. 0.3) and take the majority.
      No majority = review.
- [ ] **Two questions instead of one**: ask "is it the same variant?" and separately "list any structural difference".
      If they contradict, review. Reduces yes-bias.
- [ ] **Image-pair few-shots**: fill `knowledge/few_shots/pairs.json` with 5 to 10 hard cases from products NOT
      in the batch: round vs rectangular, sconce in chandelier gallery, same design with different arm count,
      close-up that is valid, room scene that is valid.

## Phase 4: Use the whole gallery, not one photo at a time

- [ ] **Gallery clustering**: DINO embeddings (on cutouts) for every gallery photo, group by similarity.
      A cluster with no photo close to the hero or its references is likely a different variant: send the
      whole cluster to strict check and show it grouped in the UI.
- [ ] **Supplier variant images**: if the Tmall variant thumbnails are available (`is_variant_img`), compare each
      candidate to every variant thumbnail. If it is clearly closer to another variant than to the hero, mark invalid.
      This gives the model a real "other option" to compare against.

## Phase 5: Keep it accurate

- [ ] **Regression set**: freeze about 50 labeled pairs in `tests/fixtures/`, mocked and real-run modes.
- [ ] **GitHub Action**: run eval on the regression set for every PR; fail if missed bad photos increase.
- [ ] **Error loop** after each eval: open `eval_errors.csv`. Every missed bad photo must lead to one of:
      a new rule, a new few-shot pair, or a threshold change. Nothing else gets added.
- [ ] Re-tune `VALID_CONF`, `INVALID_CONF`, `ATTR_CONF` on `dev` only, confirm once on `holdout`.

---

## Prompt guidelines (for `prompts/*.md`)

- Short beats long. Small vision models get worse with long rulebooks.
- Ask for counts and shapes, not "look carefully".
- Visual evidence only: no OCR, model codes, dimensions, titles or backgrounds.
- Always allow `unsure`. Low confidence goes to review, never to invalid.
- One rule per line, each backed by a labeled example that needed it.

## Priority order

1. Phase 0 (flash only + labels + baseline)
2. Zoom crops, cutout + original, multiple references (Phase 1)
3. Counting-first prompt + voting (Phase 3)
4. Strict mode (Phase 2), keep it only if eval shows it is worth the cost
5. Gallery clustering, then supplier variant images (Phase 4)
6. Regression set + GitHub Action (Phase 5)

## Results log

| Date | Change | Missed bad | Invalid caught | False flags | Review rate | Calls/photo |
| --- | --- | --- | --- | --- | --- | --- |
| | Baseline v3 | | | | | |
