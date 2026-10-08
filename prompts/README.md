# Qwen prompt files

These files ARE sent to Qwen. Edit them to change how the checker thinks; no code change needed.
The server reloads them on every run.

| File | Used for |
| --- | --- |
| `describe.md` | Step 1: read ONE image and return its visible attributes (cached per image) |
| `compare.md` | Step 3: side-by-side hero vs candidate verdict |
| `critic_veto.md` | Second opinion when the inspector says "same" (catches missed bad photos) |
| `critic_defend.md` | Second opinion when the inspector says "different" with no attribute support (catches false flags) |
| `../knowledge/rules/*.md` | Category rules pasted into `{{rules}}` (general + chandelier or pendant) |
| `../knowledge/few_shots/pairs.json` | Optional real image-pair examples (see `pairs.example.json`) |

Rules for editing:
- Visual evidence only. Never ask Qwen to read text, model codes, dimensions or catalog data.
- When unsure, the model must say `unsure`. Low confidence goes to human review, never auto-invalid.
- Keep each file short. Long prompts make small vision models worse, not better.
- After any edit, re-run `python eval.py --labels labels.csv --results ai_results.json --split holdout`.

Placeholders filled by code: `{{rules}}`, `{{hero_attrs}}`, `{{cand_attrs}}`, `{{operator_notes}}`, `{{inspector_reason}}`.
