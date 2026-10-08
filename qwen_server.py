"""Live dashboard server using the visual-only product matcher.

The dashboard flow is now:
1. exact duplicate check
2. visual-only matcher (dHash + optional DINOv2)
3. Qwen image-to-image verification
4. valid / invalid / review / error

OCR, specs, model codes, titles, URLs and backgrounds are not used as match evidence.
"""
from __future__ import annotations

import csv
import json
import os
import time
import urllib.parse
from http.server import SimpleHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

from checker_utils import normalize_url, load_json, save_json, products_from_csv
from visual_only_matcher import compare, init_dino

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("PORT", "8089"))
CSV_FILE = os.environ.get("CSV_FILE", "InvalidSideImages.csv")
OUTPUT_JSON = os.environ.get("OUTPUT_JSON", "ai_results.json")
OUTPUT_CSV = os.environ.get("OUTPUT_CSV", "InvalidSideImages_Verified.csv")
FEEDBACK_FILE = os.path.join("knowledge", "feedback", "overrides.json")

os.makedirs(os.path.dirname(FEEDBACK_FILE), exist_ok=True)


def load_products():
    preloaded = load_json("preloaded_data.json", None)
    if isinstance(preloaded, list):
        return {p.get("handle"): p for p in preloaded if p.get("handle")}
    return products_from_csv(CSV_FILE) if os.path.exists(CSV_FILE) else {}


def feedback():
    data = load_json(FEEDBACK_FILE, {})
    return data.get("overrides", {}) if isinstance(data, dict) else {}


def save_feedback(payload):
    data = load_json(FEEDBACK_FILE, {"overrides": {}})
    data.setdefault("overrides", {})[f"{payload.get('handle')}::{payload.get('url')}"] = {
        "handle": payload.get("handle"), "url": payload.get("url"),
        "position": payload.get("position", "extra"),
        "is_valid": bool(payload.get("is_valid", True)),
        "note": payload.get("note", "Human review"),
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    save_json(FEEDBACK_FILE, data)


def result_for(handle, hero_url, side, overrides):
    url, position = side.get("url"), side.get("position", "extra")
    override = overrides.get(f"{handle}::{url}") or overrides.get(f"{handle}::{normalize_url(url)}")
    if override:
        valid = bool(override.get("is_valid"))
        return {
            "url": url, "position": position, "score": 100.0,
            "status": "match" if valid else "mismatch", "is_valid": valid,
            "verdict": "valid" if valid else "invalid", "needs_review": False,
            "reason": "Human review override", "human_override": True,
        }
    if normalize_url(url) == normalize_url(hero_url):
        return {
            "url": url, "position": position, "score": 100.0,
            "status": "match", "is_valid": True, "verdict": "valid",
            "needs_review": False, "duplicate_of_hero": True,
            "reason": "Same image as hero; duplicate excluded from AI matching.",
        }
    try:
        out = compare(hero_url, url)
        verdict = out.get("verdict", "review")
        confidence = float(out.get("score") or 0)
        if 0.0 < confidence <= 1.0:
            confidence *= 100.0
        reason = (out.get("qwen") or {}).get("reason") or "Visual comparison completed."
        if verdict == "error":
            reason = (out.get("qwen") or {}).get("reason") or "Visual verification failed."
        status = "match" if verdict == "valid" else ("review" if verdict == "review" else "mismatch")
        return {
            "url": url, "position": position, "score": round(confidence, 1),
            "status": status,
            "is_valid": verdict == "valid", "verdict": verdict,
            "needs_review": verdict in ("review", "error"), "reason": reason,
            "visual_evidence": {
                "dino_cosine": out.get("dino_cosine"),
                "dhash_similarity": out.get("dhash_similarity"),
                "policy": out.get("evidence_policy"),
            },
        }
    except Exception as exc:
        return {
            "url": url, "position": position, "score": 0,
            "status": "mismatch", "is_valid": False, "verdict": "error",
            "needs_review": True, "reason": f"Visual matcher error: {exc}",
        }


def export_csv(results):
    if not os.path.exists(CSV_FILE):
        return
    with open(CSV_FILE, encoding="utf-8") as source:
        reader = csv.DictReader(source)
        fields = list(reader.fieldnames or [])
        for field in ("Is Side Image Invalid", "AI Verdict", "AI Match Score", "AI Reason"):
            if field not in fields:
                fields.append(field)
        rows = []
        for row in reader:
            handle = (row.get("Handle") or "").strip()
            src = normalize_url(row.get("Image Src") or "")
            product = results.get(handle, {})
            match = next((x for x in product.get("item_photos", []) if normalize_url(x.get("url")) == src), None)
            if match:
                verdict = match.get("verdict", "review")
                row["Is Side Image Invalid"] = {"valid": "NO", "invalid": "YES"}.get(verdict, "REVIEW")
                row["AI Verdict"] = verdict
                row["AI Match Score"] = f"{match.get('score', 0)}%"
                row["AI Reason"] = match.get("reason", "")
            rows.append(row)
    with open(OUTPUT_CSV, "w", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=fields)
        writer.writeheader(); writer.writerows(rows)


class Handler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        super().end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/stream_verify":
            return self.stream_verify(parsed)
        super().do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/save_feedback":
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            save_feedback(payload)
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
            self.wfile.write(b'{"success":true}')
            return
        self.send_error(404)

    def send_event(self, name, payload):
        self.wfile.write(f"event: {name}\ndata: {json.dumps(payload, default=str)}\n\n".encode())
        self.wfile.flush()

    def stream_verify(self, parsed):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        products = load_products()
        target = urllib.parse.parse_qs(parsed.query).get("handle", [None])[0]
        selected = [(target, products[target])] if target in products else list(products.items())
        total = sum(len(p.get("item_photos", [])) for _, p in selected)
        overrides = feedback()
        results = load_json(OUTPUT_JSON, {})
        checked = 0
        counts = {"valid": 0, "invalid": 0, "review": 0, "error": 0}
        self.send_event("start", {"total_photos": total, "products_count": len(selected)})
        for handle, product in selected:
            hero = (product.get("model_photo") or {}).get("url")
            if not hero:
                continue
            items = []
            for side in product.get("item_photos", []):
                checked += 1
                self.send_event("checking", {"handle": handle, "position": side.get("position"), "current": checked, "total": total, "url": side.get("url")})
                result = result_for(handle, hero, side, overrides)
                items.append(result)
                counts[result["verdict"]] += 1
                self.send_event("photo_result", {"handle": handle, "position": result["position"], "result": result, "current": checked, "total": total})
            results[handle] = {"title": product.get("title", handle), "model_photo_url": hero,
                              "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                              "engine": "visual-only-qwen", "item_photos": items}
            save_json(OUTPUT_JSON, results)
        export_csv(results)
        self.send_event("complete", {"total_checked": checked, "counts": counts,
                                      "message": f"Done: {counts['valid']} valid, {counts['invalid']} invalid, {counts['review']} review, {counts['error']} errors."})


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


if __name__ == "__main__":
    try:
        init_dino()
    except Exception as exc:
        print(f"DINO optional initialization skipped: {exc}")
    print(f"Visual-only Qwen dashboard server: http://localhost:{PORT}/")
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()
