"""Build labels.csv for eval.py.

  python make_labels.py                      # all gallery photos, operator clicks pre-filled
  python make_labels.py --csv other.csv --out labels.csv --holdout 30

- One row per gallery photo (hero and in-gallery duplicates are skipped).
- label: operator decision from knowledge/feedback/overrides.json (the UI's valid/invalid clicks),
  else whatever is already in --out, else blank. Blank rows are ignored by eval.py.
- split: kept if already set, otherwise stable by URL hash (same photo always lands in the same split).
- image_type: kept if already set, otherwise taken from ai_results.json when the checker described it.

Then fill the blank labels (valid / invalid) and run:
  python eval.py --labels labels.csv --results ai_results.json
  python eval.py --labels labels.csv --results ai_results.json --split holdout
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import os

from checker_utils import load_json, normalize_url, products_from_csv
from pipeline import product_category

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FIELDS = ["handle", "url", "label", "category", "image_type", "mismatch_reason", "split"]


def load_products(csv_path):
    data = load_json(os.path.join(BASE_DIR, "preloaded_data.json"), None)
    if isinstance(data, list) and not os.path.exists(csv_path):
        return {p["handle"]: p for p in data if p.get("handle")}
    return products_from_csv(csv_path)


def stable_split(url, holdout_pct):
    h = int(hashlib.sha1(normalize_url(url).encode("utf-8")).hexdigest()[:8], 16) % 100
    return "holdout" if h < holdout_pct else "dev"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=os.environ.get("CSV_FILE", "InvalidSideImages.csv"))
    ap.add_argument("--out", default="labels.csv")
    ap.add_argument("--results", default="ai_results.json")
    ap.add_argument("--overrides", default=os.path.join("knowledge", "feedback", "overrides.json"))
    ap.add_argument("--holdout", type=int, default=30, help="percent of photos in the holdout split")
    args = ap.parse_args()

    products = load_products(args.csv)
    overrides = {}
    for ov in ((load_json(args.overrides, {}) or {}).get("overrides", {}) or {}).values():
        if ov.get("handle") and ov.get("url"):
            overrides[(ov["handle"].strip(), normalize_url(ov["url"]))] = "valid" if ov.get("is_valid") else "invalid"

    existing = {}
    if os.path.exists(args.out):
        with open(args.out, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                existing[((r.get("handle") or "").strip(), normalize_url(r.get("url")))] = r

    ai_types = {}
    for handle, p in (load_json(args.results, {}) or {}).items():
        for it in p.get("item_photos", []):
            if it.get("image_type"):
                ai_types[(handle, normalize_url(it.get("url")))] = it["image_type"]

    rows, conflicts = [], 0
    for handle, prod in products.items():
        hero = normalize_url((prod.get("model_photo") or {}).get("url"))
        cat = product_category(prod)
        seen = {hero}
        for ph in prod.get("item_photos", []):
            url = (ph.get("url") or "").strip()
            n = normalize_url(url)
            if not url or n in seen:
                continue
            seen.add(n)
            key = (handle, n)
            old = existing.get(key, {})
            old_label = (old.get("label") or "").strip().lower()
            label = overrides.get(key) or old_label
            if overrides.get(key) and old_label and old_label != overrides[key]:
                conflicts += 1
            rows.append({
                "handle": handle, "url": url, "label": label,
                "category": (old.get("category") or "").strip() or cat,
                "image_type": (old.get("image_type") or "").strip() or ai_types.get(key, ""),
                "mismatch_reason": (old.get("mismatch_reason") or "").strip(),
                "split": (old.get("split") or "").strip() or stable_split(url, args.holdout),
            })

    with open(args.out, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)

    lab = [r for r in rows if r["label"] in ("valid", "invalid")]
    inv = sum(1 for r in lab if r["label"] == "invalid")
    hold = sum(1 for r in rows if r["split"] == "holdout")
    print(f"Wrote {args.out}: {len(rows)} photos from {len(products)} products")
    print(f"  labelled: {len(lab)} ({len(lab) - inv} valid / {inv} invalid), blank: {len(rows) - len(lab)}")
    print(f"  split: {len(rows) - hold} dev / {hold} holdout")
    if conflicts:
        print(f"  {conflicts} labels replaced by a newer operator decision")
    if len(lab) < 200 or inv < 60:
        print("  NOTE: target is 200-300 labelled photos with 60+ invalid before tuning thresholds.")


if __name__ == "__main__":
    main()
