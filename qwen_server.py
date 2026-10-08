"""Item Checker dashboard server (v3).

Serves the simple UI and streams results over Server-Sent Events.
All checking logic lives in pipeline.py; Qwen prompts live in prompts/*.md.

  python qwen_server.py   ->  http://localhost:8089
"""
from __future__ import annotations

import csv
import io
import json
import os
import sys
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import HTTPServer, SimpleHTTPRequestHandler
from socketserver import ThreadingMixIn

import pipeline as pl
import visual_only_matcher as vm
from checker_utils import load_json, normalize_url, products_from_csv, save_json

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_DIR = pl.BASE_DIR
os.chdir(BASE_DIR)
PORT = int(os.environ.get("PORT", "8089"))
WORKERS = int(os.environ.get("WORKERS", "4"))
CSV_FILE = os.environ.get("CSV_FILE", "InvalidSideImages.csv")
PRELOADED = "preloaded_data.json"
OUTPUT_JSON = os.environ.get("OUTPUT_JSON", "ai_results.json")
OUTPUT_CSV = os.environ.get("OUTPUT_CSV", "InvalidSideImages_Verified.csv")
FEEDBACK_FILE = os.path.join("knowledge", "feedback", "overrides.json")
os.makedirs(os.path.dirname(FEEDBACK_FILE), exist_ok=True)


class ClientGone(Exception):
    pass


# ---------------- data ----------------
def load_products():
    data = load_json(PRELOADED, None)
    if isinstance(data, list):
        return {p["handle"]: p for p in data if p.get("handle")}
    return products_from_csv(CSV_FILE) if os.path.exists(CSV_FILE) else {}


def load_overrides():
    raw = (load_json(FEEDBACK_FILE, {}) or {}).get("overrides", {})
    out = {}
    for ov in raw.values():
        if ov.get("handle") and ov.get("url"):
            out[f"{ov['handle']}::{normalize_url(ov['url'])}"] = ov
    return out


def save_override(p):
    data = load_json(FEEDBACK_FILE, {"overrides": {}})
    data.setdefault("overrides", {})[f"{p['handle']}::{normalize_url(p['url'])}"] = {
        "handle": p["handle"], "url": p["url"], "position": p.get("position", "extra"),
        "is_valid": bool(p.get("is_valid")), "note": p.get("note") or "Operator review",
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    save_json(FEEDBACK_FILE, data)


def operator_notes(handle, overrides):
    lines = [f"- Pos {o.get('position')}: operator marked {'VALID' if o.get('is_valid') else 'INVALID'}"
             for o in overrides.values() if o.get("handle") == handle][:8]
    return ("\nOperator decisions on other photos of this product:\n" + "\n".join(lines)) if lines else ""


def _cv_lookup(cv, handle, url):
    p = (cv or {}).get(handle) or {}
    k = normalize_url(url)
    return next((i for i in p.get("item_photos", []) if normalize_url(i.get("url")) == k), None)


# ---------------- verification ----------------
def verify_product(handle, product, overrides, cv, fresh):
    """Yields results for every gallery photo, in completion order."""
    hero = (product.get("model_photo") or {}).get("url")
    if not hero:
        return
    category = pl.product_category(product)
    notes = operator_notes(handle, overrides)
    hero_n = normalize_url(hero)

    jobs, dups, first_pos = {}, [], {}
    for side in product.get("item_photos", []):
        url, pos = side.get("url"), side.get("position", "extra")
        n = normalize_url(url)
        ov = overrides.get(f"{handle}::{n}")
        if ov:
            ok = bool(ov.get("is_valid"))
            yield pl.result(url, pos, "valid" if ok else "invalid", 100, "Operator decision", human_override=True)
        elif n == hero_n:
            yield pl.result(url, pos, "valid", 100, "Same image as hero.", duplicate_of_hero=True)
        elif n in jobs:
            dups.append((n, url, pos))
        else:
            jobs[n] = (url, pos)
            first_pos[n] = pos

    if not jobs:
        return
    hero_attrs = None
    try:
        hero_attrs = pl.describe(hero)
    except Exception as exc:
        pl.debug_log("hero_describe_failed", handle=handle, error=str(exc))

    done = {}
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futs = {pool.submit(pl.check_pair, hero, url, pos, category, handle, hero_attrs,
                            _cv_lookup(cv, handle, url), notes, not fresh): n
                for n, (url, pos) in jobs.items()}
        for fut in as_completed(futs):
            n = futs[fut]
            try:
                res = fut.result()
            except Exception as exc:
                url, pos = jobs[n]
                res = pl.result(url, pos, "error", 0, f"Checker error: {exc}")
            done[n] = res
            yield res
    for n, url, pos in dups:
        r = dict(done[n])
        r.update(url=url, position=pos, duplicate_of_position=first_pos[n],
                 reason=f"Duplicate of Pos {first_pos[n]}. {done[n].get('reason', '')}")
        yield r


def export_csv(results):
    """Writes the verified CSV next to the source CSV. Returns the path or None."""
    if not os.path.exists(CSV_FILE):
        return None
    with open(CSV_FILE, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fields = list(reader.fieldnames or [])
        for col in ("Is Side Image Invalid", "AI Verdict", "AI Match Score", "AI Reason"):
            if col not in fields:
                fields.append(col)
        rows = []
        for row in reader:
            handle = (row.get("Handle") or "").strip()
            src = normalize_url(row.get("Image Src") or "")
            pos = (row.get("Image Position") or "").strip()
            p = results.get(handle) or {}
            if src and src == normalize_url(p.get("model_photo_url")) and pos in ("1", ""):
                row.update({"Is Side Image Invalid": "NO", "AI Verdict": "hero", "AI Match Score": "100%",
                            "AI Reason": "Hero photo"})
            else:
                items = p.get("item_photos", [])
                m = next((i for i in items if normalize_url(i.get("url")) == src and str(i.get("position")) == pos), None) \
                    or next((i for i in items if normalize_url(i.get("url")) == src), None)
                if m:
                    v = m.get("verdict", "review")
                    row["Is Side Image Invalid"] = {"valid": "NO", "invalid": "YES"}.get(v, "REVIEW")
                    row["AI Verdict"] = v
                    row["AI Match Score"] = f"{m.get('score', 0)}%"
                    row["AI Reason"] = m.get("reason", "")
            rows.append(row)
    with open(OUTPUT_CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    return OUTPUT_CSV


def results_csv_bytes(results):
    """Fallback export when there is no source CSV: one row per checked photo."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Handle", "Position", "Image Src", "AI Verdict", "AI Match Score", "AI Reason"])
    for handle, p in results.items():
        for i in p.get("item_photos", []):
            w.writerow([handle, i.get("position"), i.get("url"), i.get("verdict"), f"{i.get('score', 0)}%", i.get("reason", "")])
    return buf.getvalue().encode("utf-8")


# ---------------- HTTP ----------------
class Handler(SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        if "/api/" in (self.path or ""):
            super().log_message(fmt, *args)

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        super().end_headers()

    def _json(self, code, payload):
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path == "/api/products":
            prods = load_products()
            return self._json(200, {
                "products": [{"handle": h, "title": p.get("title", h), "type": p.get("type", ""),
                              "hero": (p.get("model_photo") or {}).get("url"),
                              "photos": [{"url": s.get("url"), "position": s.get("position", "extra")}
                                         for s in p.get("item_photos", [])]} for h, p in prods.items()],
                "api_key_set": bool(pl.API_KEY), "model": pl.QWEN_MODEL,
            })
        if u.path == "/api/stream_verify":
            return self.stream(q.get("handle", [None])[0], q.get("fresh", ["0"])[0] == "1")
        if u.path == "/api/export_csv":
            results = load_json(OUTPUT_JSON, {})
            path = export_csv(results)
            body = open(path, "rb").read() if path else results_csv_bytes(results)
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="item_checker_results.csv"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        return super().do_GET()

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/api/save_feedback":
            try:
                n = int(self.headers.get("Content-Length", 0))
                p = json.loads(self.rfile.read(n).decode("utf-8"))
                if not (p.get("handle") and p.get("url")):
                    raise ValueError("handle and url required")
                save_override(p)
                # keep ai_results.json in sync so the CSV export reflects the operator
                results = load_json(OUTPUT_JSON, {})
                for item in (results.get(p["handle"]) or {}).get("item_photos", []):
                    if normalize_url(item.get("url")) == normalize_url(p["url"]):
                        ok = bool(p.get("is_valid"))
                        item.update(pl.result(item["url"], item.get("position"), "valid" if ok else "invalid",
                                              100, "Operator decision", human_override=True))
                save_json(OUTPUT_JSON, results)
                return self._json(200, {"success": True})
            except Exception as exc:
                return self._json(400, {"success": False, "error": str(exc)})
        self.send_error(404)

    def event(self, name, payload):
        try:
            self.wfile.write(f"event: {name}\ndata: {json.dumps(payload, default=str)}\n\n".encode("utf-8"))
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            raise ClientGone()

    def stream(self, target, fresh):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        if not pl.API_KEY:
            return self.event("fatal", {"message": "DASHSCOPE_API_KEY is missing. Add it to .env and restart."})
        products = load_products()
        selected = [(target, products[target])] if target in products else list(products.items())
        total = sum(len(p.get("item_photos", [])) for _, p in selected)
        overrides = load_overrides()
        cv = load_json(pl.CV_RESULTS_JSON, {})
        results = load_json(OUTPUT_JSON, {})
        counts = {"valid": 0, "invalid": 0, "review": 0, "error": 0}
        n = 0
        try:
            self.event("start", {"total": total})
            for handle, prod in selected:
                hero = (prod.get("model_photo") or {}).get("url")
                if not hero:
                    continue
                items = []
                for res in verify_product(handle, prod, overrides, cv, fresh):
                    n += 1
                    counts[res["verdict"]] += 1
                    items.append(res)
                    self.event("photo", {"handle": handle, "result": res, "current": n, "total": total})
                results[handle] = {"title": prod.get("title", handle), "model_photo_url": hero,
                                   "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                                   "engine": f"{pl.PIPELINE_VERSION}:{pl.QWEN_MODEL}", "item_photos": items}
                save_json(OUTPUT_JSON, results)
            export_csv(results)
            self.event("complete", {"counts": counts, "total": n})
        except ClientGone:
            save_json(OUTPUT_JSON, results)
            print("  client stopped the run")


class Server(ThreadingMixIn, HTTPServer):
    daemon_threads = True


if __name__ == "__main__":
    vm.init_dino()
    print(f"Item Checker v3: http://localhost:{PORT}/")
    print(f"  model={pl.QWEN_MODEL}  escalation={pl.ESCALATION_MODEL if pl.ENABLE_ESCALATION else 'off'}  workers={WORKERS}")
    print(f"  thresholds valid>={pl.VALID_CONF} invalid>={pl.INVALID_CONF} attr>={pl.ATTR_CONF}")
    if not pl.API_KEY:
        print("  WARNING: DASHSCOPE_API_KEY not set, checks will not run.")
    Server(("", PORT), Handler).serve_forever()
