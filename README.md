# Item Checker Demo | Akeneo PIM Product Photo Verification

An AI-powered quality control and audit platform for checking whether gallery photos show the same physical product as the hero photo.

## Current visual-only flow

The dashboard flow is: **duplicate check → proportion-safe preprocessing → DINOv2 visual similarity → Qwen image-to-image verification → valid / invalid / review / error**.

On a fresh page load, gallery photos must display **Not checked** until the user runs **AI Check Photos**. Old `ai_results.json` verdicts must not be presented as fresh results. The UI keeps one gallery-level verification action instead of duplicate batch and header check buttons; manual review and export controls remain available.

The matcher compares only visible product appearance: silhouette, proportions, geometry, cords/rods, arms, tiers, heads, finish, material and distinctive physical details. It must not use OCR, printed model codes, dimensions, titles, filenames, URLs, catalog metadata or background similarity as match evidence.

## Run locally

```bash
pip install pillow ImageHash openai
python qwen_server.py
```

Open `http://localhost:8089`. Set `DASHSCOPE_API_KEY` in `.env` for Qwen verification. DINOv2 is optional. Borderline results stay `review`; API failures stay `error`, never `invalid`.

## UI bootstrap

Include `dashboard_reset.js` after `app.js` in `index.html` while validating the new flow. It removes stale verdict badges on load, changes cards to **Not checked**, and removes duplicate verification buttons. Once the behavior is confirmed, move the reset into the main render state and remove the bootstrap script.
