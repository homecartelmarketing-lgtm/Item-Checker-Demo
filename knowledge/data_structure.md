# Knowledge folder

Text in `rules/` is sent to Qwen on every check (general rules + the product's category rules).
Keep it short, visual-only, and consistent with `VISUAL_MATCHING_SPEC.md`.

- `rules/general_lighting.md`: applies to every product
- `rules/chandeliers.md`: added when the product type/title says chandelier
- `rules/pendant_lights.md`: added when it says pendant
- `few_shots/pairs.json`: optional real image-pair examples (copy `pairs.example.json`). A product never sees its own examples.
- `feedback/overrides.json`: operator ✓ / ✗ decisions from the UI. They always win.
- `brands/`: reference notes for humans only. Not sent to Qwen (model codes are text, and text is not match evidence).

The task prompts themselves live in `../prompts/`.
