# Item Checker Demo | Akeneo PIM Product Photo Verification

An AI-powered quality control and audit platform for checking whether gallery photos show the same physical product as the hero photo.

## Final visual-only matching flow

The active matching design is: **duplicate check → proportion-safe preprocessing → DINOv2 visual similarity → Qwen image-to-image verification → valid / invalid / review / error**.

The matcher compares only visible product appearance: silhouette, proportions, geometry, cords/rods, arms, tiers, heads, finish, material and distinctive physical details. It must not use OCR, printed model codes, dimensions, titles, filenames, URLs, catalog metadata or background similarity as match evidence.

The standalone production matcher is `visual_only_matcher.py`:

```bash
pip install pillow ImageHash
python visual_only_matcher.py --reference hero.jpg --candidates side1.jpg side2.jpg
```

For URL-to-URL Qwen verification, set `DASHSCOPE_API_KEY`; the API model defaults to `qwen3.8-flash` and can be changed with `QWEN_MODEL`. DINOv2 is optional and loads when PyTorch/model dependencies are available. Borderline results stay `review`; API failures stay `error`, never `invalid`.

The existing dashboard and `qwen_server.py` remain backward-compatible while this matcher is being validated against the next test run. After validation, wire `visual_only_matcher.compare()` into the dashboard stream endpoint and replace the legacy attribute/spec decision path.

## Existing components

- `index.html`, `app.js`, `style.css`: dashboard
- `qwen_server.py`: current streaming verification server
- `auto_checker.py`: offline CV evidence pipeline
- `visual_only_matcher.py`: new visual-only matcher
- `knowledge/`: rules, feedback overrides and audit exports
- `InvalidSideImages.csv`: input catalog data
