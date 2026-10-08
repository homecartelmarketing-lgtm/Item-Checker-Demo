# Visual-Only Product Matching Specification

This checker compares the Image 1 hero photo with each bottom/gallery photo to decide whether they show the same physical product variant.

## Decision flow

1. Normalize URLs and run exact/dHash duplicate detection.
2. Preprocess both images with aspect-ratio-preserving resize and optional background/object crop. Never stretch the product.
3. Use visual retrieval (DINOv2 embedding + cosine similarity, optionally FAISS for larger galleries) to rank candidate regions.
4. Use Qwen as a final image-to-image verifier, not as the only similarity engine.
5. Return only one of: `valid`, `invalid`, `review`, or `error`.

## What the model must compare

- Overall silhouette and proportions
- Product geometry and topology
- Number and placement of cords, rods, arms, tiers, or heads
- Arrangement of crystals, glass, shades, and distinctive physical details
- Finish/color only when visibly reliable

## What must not decide the match

Ignore OCR, model codes, dimensions, catalog specifications, product titles, brand names, filenames, URLs, and background similarity. Text may be logged as diagnostic evidence, but it must never create a match.

## Qwen verification prompt

```text
Image 1 is the reference product photo. Image 2 is a candidate gallery photo.
Decide whether both images show the SAME physical product design and variant.
Compare only visible physical appearance. Ignore all text, OCR, dimensions,
model codes, brand, catalog metadata, filenames, URLs, and background.
Different camera angle, crop, lighting, room scene, or close-up does not make
a product different. A clearly different silhouette, geometry, cord/rod count,
number of heads, tier structure, or fixture type does make it different.
If the candidate does not show enough of the product to decide, return unsure.
Return JSON only:
{
  "verdict": "same_variant | different_variant | unsure",
  "confidence": 0,
  "reason": "one short visual reason",
  "evidence": ["short visible feature 1", "short visible feature 2"]
}
```

## Threshold policy

- High-confidence same variant: `valid`
- High-confidence different variant with a concrete visible conflict: `invalid`
- Borderline, occluded, or detail-only evidence: `review`
- API/download/parse failure: `error`, never `invalid`

Thresholds must be calibrated against labeled same-product and different-product examples. Do not treat the raw percentage from one model as accuracy until evaluation confirms it.
