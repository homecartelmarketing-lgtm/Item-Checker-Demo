"""
Accuracy evaluation for the Item Checker.
=========================================
Compares AI results against human labels and prints the numbers that matter.

Usage:
  python eval.py --labels labels.csv --results ai_results.json
  python eval.py --labels labels.csv --results cv_results.json --split holdout

labels.csv columns (see labels_template.csv):
  handle, url, label (valid|invalid), category, image_type, mismatch_reason, split (dev|holdout)

Key metrics:
  - MISSED BAD PHOTOS: invalid photo the AI auto-passed as valid. Most important. Keep near 0.
  - FALSE FLAGS: valid photo the AI auto-flagged invalid. Costs operator time.
  - REVIEW RATE: share sent to a human. Target <= 15-20%.
  - Auto accuracy: accuracy on photos the AI decided on its own (excludes review/error).
"""

import argparse
import csv
import json
from collections import defaultdict, Counter

from checker_utils import normalize_url, load_json


def verdict_of(item):
    v = item.get("verdict")
    if v in ("valid", "invalid", "review", "error"):
        return v
    return "valid" if item.get("is_valid") else "invalid"


def load_labels(path, split=None):
    labels = []
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            lab = (row.get("label") or "").strip().lower()
            if lab not in ("valid", "invalid"):
                continue
            if split and (row.get("split") or "").strip().lower() != split:
                continue
            row["label"] = lab
            labels.append(row)
    return labels


def index_results(results):
    idx = {}
    for handle, p in results.items():
        for item in p.get("item_photos", []):
            idx[(handle, normalize_url(item.get("url")))] = item
    return idx


def summarize(rows, title):
    n = len(rows)
    if not n:
        return
    cm = Counter((r["label"], r["pred"]) for r in rows)
    inv = sum(1 for r in rows if r["label"] == "invalid")
    val = n - inv
    missed = cm[("invalid", "valid")]
    false_flags = cm[("valid", "invalid")]
    review = sum(1 for r in rows if r["pred"] in ("review", "error"))
    auto = [r for r in rows if r["pred"] in ("valid", "invalid")]
    auto_ok = sum(1 for r in auto if r["pred"] == r["label"])
    caught = cm[("invalid", "invalid")] + cm[("invalid", "review")] + cm[("invalid", "error")]
    flagged = cm[("invalid", "invalid")] + cm[("valid", "invalid")]

    pct = lambda a, b: f"{(100.0 * a / b):.1f}%" if b else "n/a"
    print(f"\n=== {title} ({n} photos: {val} valid / {inv} invalid) ===")
    print(f"  MISSED BAD PHOTOS (invalid -> auto valid): {missed}  ({pct(missed, inv)} of invalid)")
    print(f"  Invalid caught (flagged or sent to review): {pct(caught, inv)}")
    print(f"  FALSE FLAGS (valid -> auto invalid):        {false_flags}  ({pct(false_flags, val)} of valid)")
    print(f"  Precision of 'invalid' flag:                {pct(cm[('invalid', 'invalid')], flagged)}")
    print(f"  Review/error rate:                          {pct(review, n)}")
    print(f"  Auto-decision accuracy:                     {pct(auto_ok, len(auto))} on {len(auto)} auto decisions")
    print("  Confusion (label -> predicted):")
    for lab in ("valid", "invalid"):
        print(f"    {lab:8s} -> " + "  ".join(f"{p}:{cm[(lab, p)]}" for p in ("valid", "invalid", "review", "error")))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", required=True)
    ap.add_argument("--results", default="ai_results.json")
    ap.add_argument("--split", default=None, help="dev or holdout")
    ap.add_argument("--errors-out", default="eval_errors.csv")
    args = ap.parse_args()

    labels = load_labels(args.labels, args.split)
    idx = index_results(load_json(args.results, {}))
    rows, missing = [], 0
    for lab in labels:
        item = idx.get(((lab.get("handle") or "").strip(), normalize_url(lab.get("url"))))
        if not item:
            missing += 1
            continue
        if item.get("duplicate_of_hero"):
            continue   # hero self-matches are not real tests
        rows.append({**lab, "pred": verdict_of(item), "score": item.get("score"), "reason": item.get("reason", "")})

    print(f"Labels: {len(labels)} | matched in results: {len(rows)} | not in results: {missing}")
    summarize(rows, f"OVERALL {args.results}" + (f" [{args.split}]" if args.split else ""))

    for key in ("category", "image_type", "mismatch_reason"):
        groups = defaultdict(list)
        for r in rows:
            groups[(r.get(key) or "").strip() or "(blank)"].append(r)
        if len(groups) > 1:
            for g, rs in sorted(groups.items()):
                summarize(rs, f"{key} = {g}")

    wrong = [r for r in rows if r["pred"] != r["label"]]
    if wrong:
        with open(args.errors_out, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["handle", "url", "label", "pred", "score", "image_type", "mismatch_reason", "reason"],
                               extrasaction="ignore")
            w.writeheader()
            w.writerows(wrong)
        print(f"\n{len(wrong)} disagreements written to {args.errors_out} (review these to tune prompts/thresholds)")


if __name__ == "__main__":
    main()
