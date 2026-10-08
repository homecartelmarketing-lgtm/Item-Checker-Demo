"""Item Checker pipeline v3: does a gallery photo show the same product variant as the hero?

Flow per photo (visual evidence only, see VISUAL_MATCHING_SPEC.md):
  0. operator override wins
  1. same image as hero (URL or dHash)        -> valid, no AI call
  2. duplicate inside the gallery              -> reuse first result
  3. Qwen "describe" each image alone          -> attributes (cached per image)
  4. hard rules in code                        -> shape / cords / tiers / heads / fixture type
  5. Qwen side-by-side compare (+ few-shots)   -> same_variant / different_variant / unsure
  6. critic: veto on matches, defend on unsupported mismatches
  7. borderline: swapped-order re-check, then escalation model
  8. verdict: valid / invalid / review / error  (errors are never invalid)

Prompts live in prompts/*.md and knowledge/rules/*.md and are reloaded on every run.

CLI:
  python pipeline.py --reference HERO_URL --candidates URL1 URL2 [--category chandelier]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time

import visual_only_matcher as vm
from checker_utils import load_json, normalize_url, save_json

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PIPELINE_VERSION = "v3.0"


# ---------------- config ----------------
def _load_env_file():
    path = os.path.join(BASE_DIR, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.split("#", 1)[0].strip())


_load_env_file()


def _f(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return float(default)


def _b(name, default):
    return str(os.environ.get(name, default)).strip().lower() in ("1", "true", "yes", "on")


API_KEY = os.environ.get("DASHSCOPE_API_KEY", "")
BASE_URL = os.environ.get("DASHSCOPE_BASE_URL", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1")
QWEN_MODEL = os.environ.get("QWEN_MODEL", "qwen3.8-flash")
ESCALATION_MODEL = os.environ.get("QWEN_ESCALATION_MODEL", "qwen-vl-max")
ENABLE_ESCALATION = _b("ENABLE_ESCALATION", "1")
ENABLE_SWAP_CHECK = _b("ENABLE_SWAP_CHECK", "1")
SWAP_BELOW = _f("SWAP_BELOW", 95)          # re-check with images swapped when confidence is below this
VALID_CONF = _f("VALID_CONF", 90)          # auto-pass needs this much (missed bad photos are the costly error)
INVALID_CONF = _f("INVALID_CONF", 85)      # auto-flag needs this much
ATTR_CONF = _f("ATTR_CONF", 70)            # attribute read-outs below this never trigger hard rules
DHASH_MAX_DIST = int(_f("DHASH_MAX_DIST", 6))
DINO_LOW = _f("DINO_LOW", 0.35)            # below this DINO cosine adds a soft conflict
CLOSEUP_POLICY = os.environ.get("CLOSEUP_POLICY", "valid_if_consistent")  # or "review"
FEWSHOT_MAX = int(_f("FEWSHOT_MAX", 2))
API_RETRIES = int(_f("API_RETRIES", 3))

PROMPTS_DIR = os.path.join(BASE_DIR, "prompts")
RULES_DIR = os.path.join(BASE_DIR, "knowledge", "rules")
FEWSHOT_FILE = os.path.join(BASE_DIR, "knowledge", "few_shots", "pairs.json")
ATTR_CACHE_FILE = os.path.join(BASE_DIR, "attr_cache.json")
PAIR_CACHE_FILE = os.path.join(BASE_DIR, "pair_cache.json")
CV_RESULTS_JSON = os.path.join(BASE_DIR, "cv_results.json")
LOG_DIR = os.path.join(BASE_DIR, "logs")
DEBUG_LOG = os.path.join(LOG_DIR, "verify_debug.jsonl")
os.makedirs(LOG_DIR, exist_ok=True)

_LOCK = threading.Lock()


class QwenError(Exception):
    pass


def debug_log(event, **data):
    rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "event": event, **data}
    with _LOCK:
        try:
            with open(DEBUG_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        except Exception:
            pass


# ---------------- prompt files ----------------
def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""


def prompt(name, **values):
    text = _read(os.path.join(PROMPTS_DIR, name + ".md"))
    if not text:
        raise RuntimeError(f"missing prompts/{name}.md")
    for k, v in values.items():
        text = text.replace("{{" + k + "}}", str(v or ""))
    return text


def product_category(product):
    text = " ".join(str(product.get(k) or "") for k in ("type", "category", "title")).lower()
    if "pendant" in text:
        return "pendant"
    if "chandelier" in text:
        return "chandelier"
    return "general"


def rules_for(category):
    parts = [_read(os.path.join(RULES_DIR, "general_lighting.md"))]
    extra = {"chandelier": "chandeliers.md", "pendant": "pendant_lights.md"}.get(category)
    if extra:
        parts.append(_read(os.path.join(RULES_DIR, extra)))
    return "\n\n".join(p for p in parts if p)


def fewshots_for(category, handle):
    pairs = load_json(FEWSHOT_FILE, [])
    if not isinstance(pairs, list):
        return []
    picked = [p for p in pairs
              if p.get("category") in (category, "general")
              and (p.get("handle") or "").strip() != (handle or "").strip()   # no leakage
              and p.get("hero_url") and p.get("candidate_url")]
    return picked[:FEWSHOT_MAX]


# ---------------- Qwen ----------------
_CLIENT = None


def client():
    global _CLIENT
    if _CLIENT is None:
        from openai import OpenAI
        _CLIENT = OpenAI(api_key=API_KEY or "missing", base_url=BASE_URL)
    return _CLIENT


def _parse_json(text):
    if not text:
        raise ValueError("empty response")
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I | re.M).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", t, flags=re.S)
        if m:
            return json.loads(m.group(0))
        raise


def call_qwen(content, tag, model=None):
    """content: list of {"type":"text"} / {"type":"image_url"} parts."""
    if not API_KEY:
        raise QwenError("DASHSCOPE_API_KEY is not set")
    model = model or QWEN_MODEL
    last = None
    for attempt in range(1, API_RETRIES + 1):
        raw = None
        try:
            resp = client().chat.completions.create(
                model=model, temperature=0, response_format={"type": "json_object"},
                messages=[{"role": "user", "content": content}],
            )
            raw = resp.choices[0].message.content
            out = _parse_json(raw)
            debug_log("qwen_ok", tag=tag, model=model, attempt=attempt, raw=raw)
            return out
        except Exception as exc:
            last = exc
            debug_log("qwen_error", tag=tag, model=model, attempt=attempt, error=f"{type(exc).__name__}: {exc}", raw=raw)
            if attempt < API_RETRIES:
                time.sleep(min(8, 1.5 * 2 ** (attempt - 1)))
    raise QwenError(f"{type(last).__name__}: {last}")


def _img(url):
    return {"type": "image_url", "image_url": {"url": vm.to_data_url(vm.fetch_image(url))}}


def _txt(text):
    return {"type": "text", "text": text}


def _num(v, default=None):
    try:
        return default if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return default


def _int(v):
    n = _num(v)
    return int(n) if n is not None and n >= 0 else None


def _pct(v):
    n = _num(v, 0.0)
    if 0 < n <= 1:
        n *= 100
    return max(0.0, min(100.0, n))


# ---------------- caches ----------------
_ATTR = None
_PAIRS = None


def _attr_cache():
    global _ATTR
    if _ATTR is None:
        _ATTR = load_json(ATTR_CACHE_FILE, {})
    return _ATTR


def _pair_cache():
    global _PAIRS
    if _PAIRS is None:
        _PAIRS = load_json(PAIR_CACHE_FILE, {})
    return _PAIRS


def _pair_key(hero, cand):
    return f"{PIPELINE_VERSION}|{QWEN_MODEL}|{normalize_url(hero)}|{normalize_url(cand)}"


def describe(url):
    key = normalize_url(url)
    cache = _attr_cache()
    if key in cache:
        return cache[key]
    attrs = call_qwen([_txt(prompt("describe")), _img(url)], tag="describe")
    with _LOCK:
        cache[key] = attrs
        save_json(ATTR_CACHE_FILE, cache)
    return attrs


def attr_summary(a):
    if not a:
        return "not available"
    keys = ["image_type", "fixture_type", "overall_shape", "suspension_count", "tier_count",
            "head_count", "primary_finish", "shade_material", "fixture_fully_visible"]
    return ", ".join(f"{k}={a.get(k)}" for k in keys if a.get(k) not in (None, "", "unknown"))


# ---------------- hard rules ----------------
_COMPARABLE = {"product_cutout", "lifestyle_room", "dimension_sheet"}
_ROUND = {"round", "oval", "tiered_cone"}
_LINEAR = {"rectangular_linear"}
_CEILING = {"chandelier", "pendant", "ceiling_flush"}


def compare_attributes(hero, cand, category):
    """(hard, soft) conflict lists. Hard = deterministic different variant."""
    hard, soft = [], []
    if not hero or not cand or (cand.get("image_type") or "") not in _COMPARABLE:
        return hard, soft
    reliable = _num(hero.get("attribute_confidence"), 0) >= ATTR_CONF and _num(cand.get("attribute_confidence"), 0) >= ATTR_CONF
    full = bool(hero.get("fixture_fully_visible", True)) and bool(cand.get("fixture_fully_visible", True))
    strong = reliable and full
    b = lambda v: str(v or "unknown").strip().lower()

    hf, cf = b(hero.get("fixture_type")), b(cand.get("fixture_type"))
    if "unknown" not in (hf, cf) and hf != cf:
        both_ceiling = hf in _CEILING and cf in _CEILING
        (soft if both_ceiling or not reliable else hard).append(f"fixture type {hf} vs {cf}")

    hs, cs = b(hero.get("overall_shape")), b(cand.get("overall_shape"))
    if "unknown" not in (hs, cs) and hs != cs:
        cross = (hs in _LINEAR and cs in _ROUND) or (cs in _LINEAR and hs in _ROUND)
        (hard if cross and strong else soft).append(f"shape {hs} vs {cs}")

    a, c = _int(hero.get("suspension_count")), _int(cand.get("suspension_count"))
    if a and c and a != c:
        (hard if strong else soft).append(f"suspension cords {a} vs {c}")

    a, c = _int(hero.get("tier_count")), _int(cand.get("tier_count"))
    if a and c and a != c:
        (hard if strong and category == "chandelier" else soft).append(f"tiers {a} vs {c}")

    a, c = _int(hero.get("head_count")), _int(cand.get("head_count"))
    if a and c and a != c:
        single_vs_multi = (a == 1) != (c == 1)
        (hard if strong and single_vs_multi else soft).append(f"heads {a} vs {c}")

    a, c = b(hero.get("primary_finish")), b(cand.get("primary_finish"))
    if "unknown" not in (a, c) and "mixed" not in (a, c) and a != c:
        soft.append(f"finish {a} vs {c}")   # lighting changes colour a lot: never hard
    return hard, soft


# ---------------- side-by-side + critic ----------------
def compare(hero_url, cand_url, hero_attrs, cand_attrs, category, handle, notes="", model=None, swap=False):
    first, second = (cand_url, hero_url) if swap else (hero_url, cand_url)
    a1, a2 = (cand_attrs, hero_attrs) if swap else (hero_attrs, cand_attrs)
    content = []
    shots = fewshots_for(category, handle)
    if shots:
        content.append(_txt("Solved examples from OTHER products (for calibration only):"))
        for i, s in enumerate(shots, 1):
            try:
                content += [_txt(f"Example {i}, reference:"), _img(s["hero_url"]),
                            _txt(f"Example {i}, candidate:"), _img(s["candidate_url"]),
                            _txt(f"Example {i} answer: {s.get('verdict')} ({s.get('reason', '')})")]
            except Exception as exc:
                debug_log("fewshot_skip", error=str(exc))
        content.append(_txt("Now the real task."))
    content += [_txt(prompt("compare", rules=rules_for(category), hero_attrs=attr_summary(a1),
                            cand_attrs=attr_summary(a2), operator_notes=notes)),
                _img(first), _img(second)]
    res = call_qwen(content, tag="compare_swap" if swap else "compare", model=model)
    v = str(res.get("verdict", "")).strip().lower()
    res["verdict"] = v if v in ("same_variant", "different_variant", "unsure") else "unsure"
    res["confidence"] = _pct(res.get("confidence"))   # missing confidence = 0, never a free pass
    return res


def critic(mode, hero_url, cand_url, category, reason):
    text = prompt("critic_veto" if mode == "veto" else "critic_defend",
                  rules=rules_for(category), inspector_reason=reason)
    res = call_qwen([_txt(text), _img(hero_url), _img(cand_url)], tag=f"critic_{mode}")
    res["overturn"] = bool(res.get("overturn"))
    res["critic_confidence"] = _pct(res.get("critic_confidence"))
    res["discrepancy_type"] = str(res.get("discrepancy_type", "other")).lower()
    return res


HARD_TYPES = {"shape", "suspension", "tiers", "heads", "fixture_type"}


def decide(hero_url, cand_url, hero_attrs, cand_attrs, category, handle, extra_soft, notes):
    hard, soft = compare_attributes(hero_attrs, cand_attrs, category)
    soft += extra_soft
    out = {"hard": hard, "soft": soft, "compare": None, "critic": None}
    ctype = (cand_attrs or {}).get("image_type", "")

    if ctype == "multiple_products":
        return {**out, "verdict": "review", "confidence": 50.0,
                "reason": "Photo shows several products; confirm which one is ours."}
    if ctype in ("packaging",):
        return {**out, "verdict": "review", "confidence": 50.0, "reason": "Packaging photo; product not visible."}

    comp = compare(hero_url, cand_url, hero_attrs, cand_attrs, category, handle, notes)
    out["compare"] = comp
    v, conf, reason = comp["verdict"], comp["confidence"], str(comp.get("reason", "")).strip()

    if hard:
        if v == "same_variant" and conf >= VALID_CONF:
            return {**out, "verdict": "review", "confidence": 50.0,
                    "reason": f"Rules found {'; '.join(hard)}, but side-by-side says same. {reason}"}
        return {**out, "verdict": "invalid", "confidence": max(conf if v == "different_variant" else 0.0, 90.0),
                "reason": f"Different variant: {'; '.join(hard)}. {reason}"}

    if ctype == "detail_closeup":
        if v == "different_variant" and conf >= INVALID_CONF:
            return {**out, "verdict": "invalid", "confidence": conf, "reason": reason}
        if CLOSEUP_POLICY == "valid_if_consistent" and v != "different_variant" and not soft:
            return {**out, "verdict": "valid", "confidence": max(conf, 85.0),
                    "reason": f"Close-up consistent with hero. {reason}"}
        return {**out, "verdict": "review", "confidence": conf, "reason": f"Close-up, variant not provable. {reason}"}

    if v == "same_variant":
        if conf < VALID_CONF:
            return {**out, "verdict": "review", "confidence": conf, "reason": f"Low-confidence match ({conf:.0f}%). {reason}"}
        c = critic("veto", hero_url, cand_url, category, reason)
        out["critic"] = c
        if c["overturn"]:
            if c["discrepancy_type"] in HARD_TYPES and c["critic_confidence"] >= INVALID_CONF:
                return {**out, "verdict": "invalid", "confidence": c["critic_confidence"],
                        "reason": f"Critic found: {c.get('discrepancy_found', '')}"}
            return {**out, "verdict": "review", "confidence": 50.0, "reason": f"Critic doubts match: {c.get('discrepancy_found', '')}"}
        if len(soft) >= 2:
            return {**out, "verdict": "review", "confidence": conf, "reason": f"Match, but {'; '.join(soft)}. {reason}"}
        return {**out, "verdict": "valid", "confidence": conf, "reason": reason}

    if v == "different_variant":
        if conf < INVALID_CONF:
            return {**out, "verdict": "review", "confidence": conf, "reason": f"Low-confidence mismatch ({conf:.0f}%). {reason}"}
        if not soft:
            c = critic("defend", hero_url, cand_url, category, reason)
            out["critic"] = c
            if c["overturn"] and c["critic_confidence"] >= VALID_CONF:
                return {**out, "verdict": "review", "confidence": 50.0,
                        "reason": f"Inspector flagged it, critic says same: {c.get('critic_reason', '')}"}
        return {**out, "verdict": "invalid", "confidence": conf, "reason": reason}

    return {**out, "verdict": "review", "confidence": conf, "reason": f"Model unsure. {reason}"}


def _swap_agrees(d, hero_url, cand_url, hero_attrs, cand_attrs, category, handle, notes):
    """Position-bias check: ask again with the images swapped."""
    s = compare(hero_url, cand_url, hero_attrs, cand_attrs, category, handle, notes, swap=True)
    want = {"valid": "same_variant", "invalid": "different_variant"}[d["verdict"]]
    return s["verdict"] == want, s


def _escalate(d, hero_url, cand_url, hero_attrs, cand_attrs, category, handle, notes):
    comp = compare(hero_url, cand_url, hero_attrs, cand_attrs, category, handle, notes, model=ESCALATION_MODEL)
    v, c, r = comp["verdict"], comp["confidence"], comp.get("reason", "")
    if not d["hard"] and v == "same_variant" and c >= VALID_CONF and len(d["soft"]) < 2 \
            and not (d.get("critic") or {}).get("overturn"):
        d.update(verdict="valid", confidence=c, reason=f"[{ESCALATION_MODEL}] {r}")
    elif v == "different_variant" and c >= INVALID_CONF:
        d.update(verdict="invalid", confidence=c, reason=f"[{ESCALATION_MODEL}] {r}")
    d["escalated"] = True
    return d


def result(url, pos, verdict, confidence, reason, **extra):
    return {
        "url": url, "position": pos, "verdict": verdict,
        "score": round(float(confidence or 0), 1),
        "is_valid": verdict == "valid",
        "status": {"valid": "match", "invalid": "mismatch", "review": "review", "error": "error"}[verdict],
        "needs_review": verdict in ("review", "error"),
        "reason": (reason or "").strip(), **extra,
    }


def check_pair(hero_url, cand_url, pos="extra", category="general", handle="", hero_attrs=None,
               cv_evidence=None, notes="", use_cache=True):
    """Full pipeline for one hero/candidate pair (no overrides, no dedup)."""
    key = _pair_key(hero_url, cand_url)
    if use_cache and key in _pair_cache():
        cached = dict(_pair_cache()[key])
        cached.update(url=cand_url, position=pos, cached=True)
        return cached
    try:
        hero_img, cand_img = vm.fetch_image(hero_url), vm.fetch_image(cand_url)
    except Exception as exc:
        return result(cand_url, pos, "error", 0, f"Image download failed: {exc}")

    sim, dist = vm.dhash_similarity(hero_img, cand_img)
    if dist <= DHASH_MAX_DIST:
        return result(cand_url, pos, "valid", 100, f"Near-duplicate of hero photo (dHash distance {dist}).",
                      duplicate_of_hero=True)
    dino = vm.dino_cosine(hero_img, cand_img)
    extra_soft = []
    if dino is not None and dino < DINO_LOW:
        extra_soft.append(f"low visual similarity (DINO {dino:.2f})")
    if cv_evidence and cv_evidence.get("cv_shape_conflict"):
        extra_soft.append("CV silhouette aspect-ratio conflict")

    try:
        hero_attrs = hero_attrs or describe(hero_url)
        cand_attrs = describe(cand_url)
        d = decide(hero_url, cand_url, hero_attrs, cand_attrs, category, handle, extra_soft, notes)
        d["escalated"], d["swap"] = False, None
        if ENABLE_SWAP_CHECK and d["verdict"] in ("valid", "invalid") and not d["hard"] and d["confidence"] < SWAP_BELOW:
            ok, s = _swap_agrees(d, hero_url, cand_url, hero_attrs, cand_attrs, category, handle, notes)
            d["swap"] = {"verdict": s["verdict"], "confidence": s["confidence"]}
            if not ok:
                d.update(verdict="review", confidence=50.0,
                         reason=f"Answer changed when images were swapped. {d['reason']}")
        if d["verdict"] == "review" and ENABLE_ESCALATION and ESCALATION_MODEL and ESCALATION_MODEL != QWEN_MODEL:
            try:
                d = _escalate(d, hero_url, cand_url, hero_attrs, cand_attrs, category, handle, notes)
            except QwenError as exc:
                debug_log("escalation_failed", url=cand_url, error=str(exc))
    except QwenError as exc:
        return result(cand_url, pos, "error", 0, f"Qwen failed after {API_RETRIES} tries: {exc}", api_error=True)
    except Exception as exc:
        return result(cand_url, pos, "error", 0, f"Checker error: {type(exc).__name__}: {exc}")

    comp, crit = d.get("compare") or {}, d.get("critic") or {}
    res = result(
        cand_url, pos, d["verdict"], d["confidence"], d["reason"],
        image_type=(cand_attrs or {}).get("image_type", ""),
        conflicts={"hard": d["hard"], "soft": d["soft"]},
        critic=crit.get("discrepancy_found") or crit.get("critic_reason") or "",
        escalated=d.get("escalated", False),
        swap_check=d.get("swap"),
        checks={k: comp.get(k, "") for k in ("shape_check", "structure_check", "finish_check")},
        evidence={"dhash_similarity": sim, "dino_cosine": dino},
        attributes={"hero": hero_attrs, "candidate": cand_attrs},
        engine=f"{PIPELINE_VERSION}:{QWEN_MODEL}",
    )
    with _LOCK:
        _pair_cache()[key] = res
        save_json(PAIR_CACHE_FILE, _pair_cache())
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reference", required=True)
    ap.add_argument("--candidates", nargs="+", required=True)
    ap.add_argument("--category", default="general", choices=["general", "chandelier", "pendant"])
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()
    vm.init_dino()
    out = [check_pair(args.reference, c, category=args.category, use_cache=not args.no_cache) for c in args.candidates]
    print(json.dumps(out, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
