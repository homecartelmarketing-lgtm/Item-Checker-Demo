"""
Qwen Real-time Vision Web Server (v2: accuracy upgrade)
=======================================================
Streams photo verification to the browser over Server-Sent Events.

What changed vs v1 (see ACCURACY_NOTES.md):
- Dedup: exact / normalised URL duplicates and near-duplicates (dHash) of the
  hero are resolved without an AI call, so they no longer inflate the stats.
- Step 1 "describe": Qwen extracts structured attributes from EACH image on its own
  (image type, fixture type, shape, cord count, tiers, heads, finish).
- Step 2 "compare in code": hard rules (round vs rectangular, cord count,
  fixture type, single vs multi-head pendant) decide clear mismatches.
- Step 3 "holistic + critic": side-by-side check at temperature 0, critic runs on
  BOTH matches (catch false passes) and weak mismatches (catch false flags).
- 4 outcomes: valid / invalid / review / error. Errors are never "mismatch".
- Borderline cases escalate to a stronger model (QWEN_ESCALATION_MODEL).
- Few-shot leakage guard, better KB routing, catalog specs from CSV.
- Every request/response logged to logs/verify_debug.jsonl for debugging.

Output stays compatible with app.js: `status` is still "match" / "mismatch",
plus new fields `verdict`, `needs_review`, `conflicts`, `attributes`.
"""

import os
import sys
import json
import csv
import time
import re
import urllib.parse
from http.server import SimpleHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

from checker_utils import (
    normalize_url, load_json, save_json, dhash_url, hamming, products_from_csv,
)

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

# -------------------------------------------------------------
# Config
# -------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_env_file():
    env_path = os.path.join(BASE_DIR, ".env")
    if os.path.exists(env_path):
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        os.environ.setdefault(k.strip(), v.strip())
        except Exception:
            pass


_load_env_file()


def _env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return float(default)


def _env_bool(name, default):
    return str(os.environ.get(name, default)).strip().lower() in ("1", "true", "yes", "on")


PORT = int(os.environ.get("PORT", 8089))
CSV_FILE = "InvalidSideImages.csv"
PRELOADED_DATA = "preloaded_data.json"
OUTPUT_JSON = "ai_results.json"
OUTPUT_CSV = "InvalidSideImages_Verified.csv"
CV_RESULTS_JSON = "cv_results.json"          # written by auto_checker.py (optional evidence)
ATTR_CACHE_FILE = "attr_cache.json"          # per-image attribute cache (saves API calls)
KNOWLEDGE_DIR = "knowledge"
FEEDBACK_FILE = os.path.join(KNOWLEDGE_DIR, "feedback", "overrides.json")
AUDIT_DIR = os.path.join(KNOWLEDGE_DIR, "audits")
LOG_DIR = "logs"
DEBUG_LOG = os.path.join(LOG_DIR, "verify_debug.jsonl")

DASHSCOPE_API_KEY = os.environ.get("DASHSCOPE_API_KEY", "")
DASHSCOPE_BASE_URL = os.environ.get("DASHSCOPE_BASE_URL", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1")
QWEN_MODEL = os.environ.get("QWEN_MODEL", "qwen3.8-flash")
ESCALATION_MODEL = os.environ.get("QWEN_ESCALATION_MODEL", "qwen-vl-max")
ENABLE_ESCALATION = _env_bool("ENABLE_ESCALATION", "1")
ENABLE_DHASH = _env_bool("ENABLE_DHASH", "1")
EXCLUDE_OWN_FEWSHOTS = _env_bool("EXCLUDE_OWN_FEWSHOTS", "1")   # avoid leaking answers into the prompt
CLOSEUP_POLICY = os.environ.get("CLOSEUP_POLICY", "valid_if_consistent")  # or "review"

VALID_CONF = _env_float("VALID_CONF", 90)        # min confidence to auto-pass a match
INVALID_CONF = _env_float("INVALID_CONF", 85)    # min confidence to auto-flag a mismatch
ATTR_CONF = _env_float("ATTR_CONF", 70)          # min attribute confidence to use a hard rule
DHASH_MAX_DIST = int(os.environ.get("DHASH_MAX_DIST", 6))
API_RETRIES = int(os.environ.get("API_RETRIES", 3))

os.makedirs(os.path.join(KNOWLEDGE_DIR, "feedback"), exist_ok=True)
os.makedirs(AUDIT_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

_QWEN_CLIENT = None


def get_client():
    global _QWEN_CLIENT
    if _QWEN_CLIENT is None:
        from openai import OpenAI
        _QWEN_CLIENT = OpenAI(api_key=DASHSCOPE_API_KEY or "missing_key", base_url=DASHSCOPE_BASE_URL)
    return _QWEN_CLIENT


class QwenError(Exception):
    pass


# -------------------------------------------------------------
# Debug logging
# -------------------------------------------------------------
def debug_log(event, **data):
    rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "event": event, **data}
    try:
        with open(DEBUG_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass


# -------------------------------------------------------------
# Qwen call with retries, temperature 0 and robust JSON parsing
# -------------------------------------------------------------
def _parse_json_text(text):
    if text is None:
        raise ValueError("empty response")
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.IGNORECASE | re.MULTILINE).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", t, flags=re.DOTALL)
        if m:
            return json.loads(m.group(0))
        raise


def call_qwen_json(prompt, image_urls, tag, model=None):
    model = model or QWEN_MODEL
    content = [{"type": "text", "text": prompt}] + [
        {"type": "image_url", "image_url": {"url": u}} for u in image_urls
    ]
    last_err = None
    for attempt in range(1, API_RETRIES + 1):
        raw = None
        try:
            resp = get_client().chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": content}],
                response_format={"type": "json_object"},
                temperature=0,
            )
            raw = resp.choices[0].message.content
            parsed = _parse_json_text(raw)
            debug_log("qwen_ok", tag=tag, model=model, attempt=attempt, images=image_urls, raw=raw)
            return parsed
        except Exception as e:
            last_err = e
            debug_log("qwen_error", tag=tag, model=model, attempt=attempt, images=image_urls,
                      error=f"{type(e).__name__}: {e}", raw=raw)
            print(f"  [qwen] {tag} attempt {attempt}/{API_RETRIES} failed: {type(e).__name__}: {e}")
            if attempt < API_RETRIES:
                time.sleep(min(8, 1.5 * (2 ** (attempt - 1))))
    raise QwenError(f"{type(last_err).__name__}: {last_err}")


def _num(v, default=None):
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _int_or_none(v):
    n = _num(v)
    return int(n) if n is not None and n >= 0 else None


# -------------------------------------------------------------
# Human overrides (active learning)
# -------------------------------------------------------------
def load_human_overrides():
    data = load_json(FEEDBACK_FILE, {})
    raw = data.get("overrides", {}) if isinstance(data, dict) else {}
    out = {}
    for key, ov in raw.items():
        out[key] = ov
        handle = ov.get("handle")
        url = ov.get("url")
        if handle and url:
            out[f"{handle}::{normalize_url(url)}"] = ov
    return out


def save_human_override(handle, url, position, is_valid, note=""):
    data = load_json(FEEDBACK_FILE, {"description": "Operator Manual Feedback & Correction Overrides", "overrides": {}})
    key = f"{handle}::{url}"
    data.setdefault("overrides", {})[key] = {
        "handle": handle,
        "url": url,
        "position": position,
        "is_valid": is_valid,
        "note": note or ("Marked Valid by Operator" if is_valid else "Flagged Invalid by Operator"),
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save_json(FEEDBACK_FILE, data)


def override_context(handle, overrides):
    """Past operator decisions for this product, injected as text context."""
    lines = []
    seen = set()
    for ov in overrides.values():
        if ov.get("handle") != handle:
            continue
        k = normalize_url(ov.get("url"))
        if k in seen:
            continue
        seen.add(k)
        lines.append(f"- Pos {ov.get('position')}: operator marked {'VALID' if ov.get('is_valid') else 'INVALID'} ({ov.get('note', '')})")
    if not lines:
        return ""
    return "\n\n[OPERATOR DECISIONS FOR OTHER PHOTOS OF THIS PRODUCT]:\n" + "\n".join(lines[:8])


# -------------------------------------------------------------
# Products
# -------------------------------------------------------------
def load_products():
    """preloaded_data.json if present, else the CSV (hero = position 1)."""
    if os.path.exists(PRELOADED_DATA):
        items = load_json(PRELOADED_DATA, None)
        if isinstance(items, list):
            return {p['handle']: p for p in items}
    if not os.path.exists(CSV_FILE):
        return {}
    return products_from_csv(CSV_FILE)


def product_category(product):
    text = " ".join([product.get('type') or '', product.get('category') or '', product.get('title') or '']).lower()
    if 'pendant' in text:
        return 'pendant'
    if 'chandelier' in text:
        return 'chandelier'
    return 'general'


# -------------------------------------------------------------
# Knowledge base routing
# -------------------------------------------------------------
def _read(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return f.read().strip()
    except Exception:
        return ""


def load_knowledge_context(product):
    title = product.get('title', '') or ''
    vendor = (product.get('vendor') or '').lower()
    cat = product_category(product)
    parts = []

    general = _read(os.path.join(KNOWLEDGE_DIR, 'rules', 'general_lighting.md'))
    if general:
        parts.append("[KB - GENERAL IMAGE RULES]:\n" + general)

    rule_file = {'pendant': 'pendant_lights.md', 'chandelier': 'chandeliers.md'}.get(cat)
    if rule_file:
        txt = _read(os.path.join(KNOWLEDGE_DIR, 'rules', rule_file))
        if txt:
            parts.append("[KB - CATEGORY RULES]:\n" + txt)

    brands_dir = os.path.join(KNOWLEDGE_DIR, 'brands')
    if os.path.isdir(brands_dir):
        for fn in os.listdir(brands_dir):
            if not fn.endswith('.md'):
                continue
            slug = fn[:-3].lower()
            if slug and (slug in vendor or slug in title.lower() or (slug == 'huanglilai' and 'huang' in vendor)):
                txt = _read(os.path.join(brands_dir, fn))
                if txt:
                    parts.append("[KB - BRAND PROFILE]:\n" + txt)

    if cat == 'chandelier':
        few = _read(os.path.join(KNOWLEDGE_DIR, 'few_shots', 'chandelier_mismatches.md'))
        name = title.split('|')[0].strip().lower()
        leaks = EXCLUDE_OWN_FEWSHOTS and name and name in few.lower()
        if few and not leaks:
            parts.append("[KB - VERIFIED FEW-SHOT EXAMPLES (other products)]:\n" + few)
        elif leaks:
            debug_log("fewshot_skipped_leakage", product=title)

    return ("\n\n" + "\n\n".join(parts)) if parts else ""


def specs_block(product):
    specs = product.get('specs', {}) or {}
    dims = " x ".join([p for p in [specs.get('length'), specs.get('width'), specs.get('height')] if p]) or specs.get('diameter') or 'N/A'
    return f"""PRODUCT CATALOG SPECIFICATIONS:
- Product Title: {product.get('title')}
- Category: {product_category(product)}
- Expected Shape: {specs.get('shape') or 'Not in catalog: infer from Image 1'}
- Expected Dimensions: {dims}
- Model Code: {specs.get('model') or product.get('sku') or 'N/A'}
- Materials: {specs.get('material') or 'N/A'}
- Vendor: {product.get('vendor') or 'N/A'}"""


# -------------------------------------------------------------
# Step 1: per-image attribute extraction (cached by URL)
# -------------------------------------------------------------
ATTR_PROMPT = """You are a lighting product cataloguer. Describe ONLY what is visible in this ONE image.
Do not guess hidden parts. If something is not visible or unclear use "unknown" / null.

Respond ONLY with valid JSON:
{
  "image_type": "product_cutout | lifestyle_room | detail_closeup | dimension_sheet | packaging | multiple_products | other",
  "fixture_type": "chandelier | pendant | wall_sconce | table_lamp | floor_lamp | ceiling_flush | other | unknown",
  "overall_shape": "rectangular_linear | round | oval | square | tiered_cone | irregular | unknown",
  "suspension_count": <integer number of hanging cords/rods from the ceiling, or null>,
  "tier_count": <integer number of stacked layers, or null>,
  "head_count": <integer number of separate lamp heads/shades (1 for a single pendant), or null>,
  "primary_finish": "gold | chrome | black | brass | white | silver | bronze | mixed | unknown",
  "shade_material": "crystal | glass | fabric | metal | acrylic | mixed | unknown",
  "fixture_fully_visible": true/false,
  "visible_text": "any dimension text or model code printed on the image, else empty",
  "attribute_confidence": 0-100,
  "description": "one short sentence"
}"""

_ATTR_CACHE = None


def _attr_cache():
    global _ATTR_CACHE
    if _ATTR_CACHE is None:
        _ATTR_CACHE = load_json(ATTR_CACHE_FILE, {})
    return _ATTR_CACHE


def extract_attributes(url):
    key = normalize_url(url)
    cache = _attr_cache()
    if key in cache:
        return cache[key]
    attrs = call_qwen_json(ATTR_PROMPT, [url], tag="attributes")
    cache[key] = attrs
    try:
        save_json(ATTR_CACHE_FILE, cache)
    except Exception:
        pass
    return attrs


# -------------------------------------------------------------
# Step 2: rule-based attribute comparison
# -------------------------------------------------------------
_COMPARABLE_TYPES = {"product_cutout", "lifestyle_room", "dimension_sheet"}
_ROUNDISH = {"round", "oval", "tiered_cone"}
_LINEAR = {"rectangular_linear"}


def compare_attributes(hero, cand, category):
    """Returns (hard_conflicts, soft_conflicts). Hard = deterministic mismatch."""
    hard, soft = [], []
    if not hero or not cand:
        return hard, soft
    if (cand.get("image_type") or "") not in _COMPARABLE_TYPES:
        return hard, soft
    reliable = (_num(hero.get("attribute_confidence"), 0) >= ATTR_CONF and
                _num(cand.get("attribute_confidence"), 0) >= ATTR_CONF)
    fully = bool(cand.get("fixture_fully_visible", True)) and bool(hero.get("fixture_fully_visible", True))

    def bucket(v):
        return (v or "unknown").strip().lower()

    # Fixture type (ceiling fixture vs sconce/table lamp etc.)
    hf, cf = bucket(hero.get("fixture_type")), bucket(cand.get("fixture_type"))
    if "unknown" not in (hf, cf) and hf != cf:
        ceiling = {"chandelier", "pendant"}
        if hf in ceiling and cf in ceiling:
            soft.append(f"fixture type {hf} vs {cf}")
        else:
            (hard if reliable else soft).append(f"fixture type {hf} vs {cf}")

    # Shape: linear vs round family is the #1 variant mix-up
    hs, cs = bucket(hero.get("overall_shape")), bucket(cand.get("overall_shape"))
    if "unknown" not in (hs, cs) and hs != cs:
        cross = (hs in _LINEAR and cs in _ROUNDISH) or (cs in _LINEAR and hs in _ROUNDISH)
        (hard if (cross and reliable and fully) else soft).append(f"shape {hs} vs {cs}")

    # Suspension cord count (dual cords = linear, single = round)
    hsus, csus = _int_or_none(hero.get("suspension_count")), _int_or_none(cand.get("suspension_count"))
    if hsus and csus and hsus != csus:
        (hard if (reliable and fully) else soft).append(f"suspension cords {hsus} vs {csus}")

    # Head count: single vs multi-head pendant is always a different variant
    hh, ch = _int_or_none(hero.get("head_count")), _int_or_none(cand.get("head_count"))
    if hh and ch and hh != ch:
        single_vs_multi = (hh == 1) != (ch == 1)
        if category == "pendant" and single_vs_multi and reliable and fully:
            hard.append(f"heads {hh} vs {ch} (single vs multi-head)")
        else:
            soft.append(f"heads {hh} vs {ch}")

    # Tiers
    ht, ct = _int_or_none(hero.get("tier_count")), _int_or_none(cand.get("tier_count"))
    if ht and ct and ht != ct:
        soft.append(f"tiers {ht} vs {ct}")

    # Finish
    hfin, cfin = bucket(hero.get("primary_finish")), bucket(cand.get("primary_finish"))
    if "unknown" not in (hfin, cfin) and "mixed" not in (hfin, cfin) and hfin != cfin:
        soft.append(f"finish {hfin} vs {cfin}")

    return hard, soft


def attr_summary(a):
    if not a:
        return "N/A"
    keys = ["image_type", "fixture_type", "overall_shape", "suspension_count", "tier_count",
            "head_count", "primary_finish", "shade_material", "visible_text"]
    return ", ".join(f"{k}={a.get(k)}" for k in keys if a.get(k) not in (None, "", "unknown"))


# -------------------------------------------------------------
# Step 3: holistic comparison + two-way critic
# -------------------------------------------------------------
def build_compare_prompt(product, hero_attrs, cand_attrs, extra_ctx):
    return f"""You are a senior Quality Control Inspector for an e-commerce lighting catalog.
Image 1 = Model Hero Reference. Image 2 = Candidate gallery image.
Decide if Image 2 shows the EXACT same product design AND variant as Image 1.

{specs_block(product)}

Independent attribute read-out (from separate single-image passes, may contain errors):
- Image 1: {attr_summary(hero_attrs)}
- Image 2: {attr_summary(cand_attrs)}
{extra_ctx}
{load_knowledge_context(product)}

EVALUATION RUBRIC:
1. Shape & topology (rectangular/linear vs round/oval vs tiered).
2. Suspension & mounting (number of cords/rods, canopy shape).
3. Structure (tiers, arms, number of heads).
4. Materials, finish colour, crystal/glass style.
5. Scene type. A styled room photo of the same model is VALID. A detail close-up is valid only if
   nothing contradicts Image 1. A different fixture type (sconce, table lamp) is INVALID.

HARD RULES:
- Rectangular/linear and round/circular versions are ALWAYS different variants.
- Single-head and multi-head pendants are ALWAYS different variants.
- If you cannot see enough to decide, answer "unsure". Do not guess.

Respond ONLY with valid JSON:
{{
  "shape_assessment": "...",
  "suspension_assessment": "...",
  "material_assessment": "...",
  "scene_type": "product_cutout | lifestyle_room | detail_closeup | dimension_sheet | packaging | other",
  "verdict": "same_variant | different_variant | unsure",
  "confidence": 0-100,
  "reason": "1-2 sentence explanation"
}}"""


def holistic_compare(product, hero_url, cand_url, hero_attrs, cand_attrs, extra_ctx, model=None):
    prompt = build_compare_prompt(product, hero_attrs, cand_attrs, extra_ctx)
    res = call_qwen_json(prompt, [hero_url, cand_url], tag="compare", model=model)
    verdict = str(res.get("verdict", "")).strip().lower()
    if verdict not in ("same_variant", "different_variant", "unsure"):
        # backwards compat with the old is_match schema
        if "is_match" in res:
            verdict = "same_variant" if bool(res.get("is_match")) else "different_variant"
        else:
            verdict = "unsure"
    res["verdict"] = verdict
    res["confidence"] = max(0.0, min(100.0, _num(res.get("confidence"), 0.0)))  # missing = 0, not 80
    return res


def load_critic_rubric():
    return _read(os.path.join(KNOWLEDGE_DIR, "rules", "critic_rubric.md"))


def run_critic(mode, product, hero_url, cand_url, inspector_reason):
    """mode='veto'   -> hunt for reasons Image 2 is a DIFFERENT variant (catch false passes)
       mode='defend' -> hunt for reasons Image 2 is the SAME variant (catch false flags)"""
    if mode == "veto":
        mandate = """The primary inspector says Image 2 is the SAME variant. Be extremely skeptical.
Actively look for a concrete structural difference: cord/rod count, rectangular vs round, tier count,
number of heads/arms, fixture type (sconce/table lamp), clearly different finish.
Only veto if you can name a SPECIFIC visible difference."""
    else:
        mandate = """The primary inspector says Image 2 is a DIFFERENT variant. Check whether that is a false alarm.
Different camera angle, lighting, background, cropping, or a room scene does NOT make it a different variant.
Only overturn if the structure (shape, cords, tiers, heads, finish) is clearly identical."""

    prompt = f"""You are the independent QC Critic.
Primary inspector reasoning: "{inspector_reason}"

{specs_block(product)}

{load_critic_rubric()}

{mandate}

Respond ONLY with valid JSON:
{{
  "overturn": true/false,
  "discrepancy_type": "none | shape | suspension | tiers | heads | fixture_type | finish | other",
  "discrepancy_found": "the exact visible difference, or 'None'",
  "critic_confidence": 0-100,
  "critic_reason": "one sentence"
}}"""
    res = call_qwen_json(prompt, [hero_url, cand_url], tag=f"critic_{mode}")
    # accept the v1 schema too
    if "overturn" not in res and "veto" in res:
        res["overturn"] = bool(res.get("veto"))
    res["overturn"] = bool(res.get("overturn"))
    res["critic_confidence"] = _num(res.get("critic_confidence"), 0.0)
    res["discrepancy_type"] = str(res.get("discrepancy_type", "other")).lower()
    return res


# -------------------------------------------------------------
# Decision fusion
# -------------------------------------------------------------
HARD_DISCREPANCIES = {"shape", "suspension", "heads", "fixture_type"}


def decide(product, hero_url, cand_url, hero_attrs, cand_attrs, cv_evidence, extra_ctx, model=None):
    """Returns dict(verdict, confidence, reason, critic, compare, hard, soft)."""
    category = product_category(product)
    hard, soft = compare_attributes(hero_attrs, cand_attrs, category)
    if cv_evidence and cv_evidence.get("cv_shape_conflict"):
        soft.append("CV silhouette aspect-ratio conflict")

    cand_type = (cand_attrs or {}).get("image_type", "")
    if cand_type == "multiple_products":
        return dict(verdict="review", confidence=50.0, critic=None, compare=None, hard=hard, soft=soft,
                    reason="Image shows multiple products; operator should confirm which one is ours.")

    comp = holistic_compare(product, hero_url, cand_url, hero_attrs, cand_attrs, extra_ctx, model=model)
    v, conf, reason = comp["verdict"], comp["confidence"], str(comp.get("reason", ""))
    out = dict(compare=comp, critic=None, hard=hard, soft=soft)

    # 1) Deterministic attribute conflict
    if hard:
        if v == "same_variant" and conf >= VALID_CONF:
            return {**out, "verdict": "review", "confidence": 50.0,
                    "reason": f"Attribute rules found {'; '.join(hard)} but side-by-side check says same variant. {reason}"}
        return {**out, "verdict": "invalid", "confidence": max(conf if v == "different_variant" else 0, 90.0),
                "reason": f"Different variant: {'; '.join(hard)}. {reason}"}

    # 2) Detail close-ups cannot prove the variant
    if cand_type == "detail_closeup":
        if v == "different_variant" and conf >= INVALID_CONF:
            return {**out, "verdict": "invalid", "confidence": conf, "reason": reason}
        if CLOSEUP_POLICY == "valid_if_consistent" and v != "different_variant" and not soft:
            return {**out, "verdict": "valid", "confidence": max(conf, 85.0),
                    "reason": f"Detail close-up consistent with hero (variant not fully verifiable). {reason}"}
        return {**out, "verdict": "review", "confidence": conf,
                "reason": f"Detail close-up: cannot confirm variant. {reason}"}

    # 3) Proposed match -> veto critic
    if v == "same_variant":
        if conf < VALID_CONF:
            return {**out, "verdict": "review", "confidence": conf, "reason": f"Low-confidence match ({conf:.0f}%). {reason}"}
        try:
            critic = run_critic("veto", product, hero_url, cand_url, reason)
        except QwenError as e:
            return {**out, "verdict": "review", "confidence": conf, "reason": f"Critic unavailable ({e}). {reason}"}
        out["critic"] = critic
        if critic["overturn"]:
            disc = critic.get("discrepancy_found", "")
            if critic["discrepancy_type"] in HARD_DISCREPANCIES and critic["critic_confidence"] >= INVALID_CONF:
                return {**out, "verdict": "invalid", "confidence": critic["critic_confidence"],
                        "reason": f"Vetoed by Critic: {disc} ({critic.get('critic_reason', '')})"}
            return {**out, "verdict": "review", "confidence": 50.0,
                    "reason": f"Critic doubts match: {disc}. {reason}"}
        if len(soft) >= 2:
            return {**out, "verdict": "review", "confidence": conf,
                    "reason": f"Match, but minor differences: {'; '.join(soft)}. {reason}"}
        return {**out, "verdict": "valid", "confidence": conf, "reason": reason}

    # 4) Proposed mismatch
    if v == "different_variant":
        if conf < INVALID_CONF:
            return {**out, "verdict": "review", "confidence": conf, "reason": f"Low-confidence mismatch ({conf:.0f}%). {reason}"}
        if not soft:
            # no attribute support for the mismatch -> ask the defender
            try:
                critic = run_critic("defend", product, hero_url, cand_url, reason)
            except QwenError as e:
                return {**out, "verdict": "review", "confidence": conf, "reason": f"Critic unavailable ({e}). {reason}"}
            out["critic"] = critic
            if critic["overturn"] and critic["critic_confidence"] >= VALID_CONF:
                return {**out, "verdict": "review", "confidence": 50.0,
                        "reason": f"Inspector flagged, critic thinks same variant: {critic.get('critic_reason', '')}"}
        return {**out, "verdict": "invalid", "confidence": conf, "reason": reason}

    # 5) Unsure
    return {**out, "verdict": "review", "confidence": conf, "reason": f"Model unsure. {reason}"}


def _cv_lookup(cv_data, handle, url):
    p = (cv_data or {}).get(handle)
    if not p:
        return None
    key = normalize_url(url)
    return next((it for it in p.get("item_photos", []) if normalize_url(it.get("url")) == key), None)


def make_result(url, pos, verdict, confidence, reason, **extra):
    is_valid = verdict == "valid"
    prefix = {"review": "NEEDS REVIEW: ", "error": "ERROR (retry): "}.get(verdict, "")
    return {
        "url": url,
        "position": pos,
        "score": round(float(confidence), 1),
        "status": "match" if is_valid else "mismatch",   # kept for app.js compatibility
        "is_valid": is_valid,
        "verdict": verdict,                                 # valid | invalid | review | error
        "needs_review": verdict in ("review", "error"),
        "reason": prefix + (reason or "").strip(),
        **extra,
    }


def verify_photo(product, hero_url, hero_attrs, hero_hash, side, overrides, cv_data, extra_ctx):
    handle = product.get('handle') or product.get('title')
    url, pos = side['url'], side.get('position', 'extra')

    # Operator override wins
    ov = overrides.get(f"{handle}::{url}") or overrides.get(f"{handle}::{normalize_url(url)}")
    if ov:
        ok = bool(ov.get('is_valid', True))
        return make_result(url, pos, "valid" if ok else "invalid", 100.0,
                           f"Operator Override ({ov.get('note', 'Verified')})", human_override=True)

    # Same picture as the hero -> valid without an API call (no more fake 100% matches)
    if normalize_url(url) == normalize_url(hero_url):
        return make_result(url, pos, "valid", 100.0, "Same image as hero photo (not counted as AI match).",
                           duplicate_of_hero=True)
    if ENABLE_DHASH and hero_hash is not None:
        dist = hamming(hero_hash, dhash_url(url))
        if dist is not None and dist <= DHASH_MAX_DIST:
            return make_result(url, pos, "valid", 100.0, f"Near-duplicate of hero photo (dHash distance {dist}).",
                               duplicate_of_hero=True)

    cv_ev = _cv_lookup(cv_data, handle, url)
    try:
        cand_attrs = extract_attributes(url)
        d = decide(product, hero_url, url, hero_attrs, cand_attrs, cv_ev, extra_ctx)
        escalated = False
        if d["verdict"] == "review" and ENABLE_ESCALATION and ESCALATION_MODEL and ESCALATION_MODEL != QWEN_MODEL:
            try:
                comp2 = holistic_compare(product, hero_url, url, hero_attrs, cand_attrs, extra_ctx, model=ESCALATION_MODEL)
                escalated = True
                v2, c2 = comp2["verdict"], comp2["confidence"]
                if not d["hard"] and v2 == "same_variant" and c2 >= VALID_CONF and len(d["soft"]) < 2 \
                        and not (d.get("critic") and d["critic"].get("overturn")):
                    d.update(verdict="valid", confidence=c2, reason=f"[Escalated to {ESCALATION_MODEL}] {comp2.get('reason', '')}")
                elif v2 == "different_variant" and c2 >= INVALID_CONF:
                    d.update(verdict="invalid", confidence=c2, reason=f"[Escalated to {ESCALATION_MODEL}] {comp2.get('reason', '')}")
            except QwenError as e:
                debug_log("escalation_failed", url=url, error=str(e))
    except QwenError as e:
        return make_result(url, pos, "error", 0.0, f"API error after {API_RETRIES} tries: {e}", api_error=True)

    comp = d.get("compare") or {}
    critic = d.get("critic") or {}
    return make_result(
        url, pos, d["verdict"], d["confidence"], d["reason"],
        image_type=(cand_attrs or {}).get("image_type", ""),
        conflicts={"hard": d["hard"], "soft": d["soft"]},
        critic_vetoed=bool(critic.get("overturn")) and d["verdict"] == "invalid" and "Vetoed" in d["reason"],
        critic_notes=critic.get("discrepancy_found", "") if critic else "",
        escalated=escalated,
        attributes={"hero": hero_attrs, "candidate": cand_attrs},
        rubric={
            "shape": comp.get('shape_assessment', ''),
            "suspension": comp.get('suspension_assessment', ''),
            "material": comp.get('material_assessment', ''),
            "scene": comp.get('scene_type', ''),
        },
        cv_evidence=cv_ev and {k: cv_ev.get(k) for k in ("dino_cosine", "ransac_inliers", "cv_shape_conflict", "verdict")},
    )


def verify_product(product, overrides, cv_data, on_photo=None):
    """Verifies all side photos of one product. on_photo(pos, url) is called before each photo."""
    handle = product.get('handle') or product.get('title')
    hero_url = (product.get('model_photo') or {}).get('url')
    results = []
    if not hero_url:
        return results

    extra_ctx = override_context(handle, overrides)
    hero_hash = dhash_url(hero_url) if ENABLE_DHASH else None
    try:
        hero_attrs = extract_attributes(hero_url)
    except QwenError as e:
        hero_attrs = None
        debug_log("hero_attr_error", handle=handle, error=str(e))

    seen = {}
    for side in product.get('item_photos', []):
        url, pos = side['url'], side.get('position', 'extra')
        if on_photo:
            on_photo(pos, url)
        key = normalize_url(url)
        if key in seen:
            first = seen[key]
            dup = dict(first)
            dup.update(position=pos, url=url, duplicate_of_position=first.get("position"),
                       reason=f"Duplicate of Pos {first.get('position')}: {first.get('reason', '')}")
            results.append(dup)
            yield dup
            continue
        res = verify_photo(product, hero_url, hero_attrs, hero_hash, side, overrides, cv_data, extra_ctx)
        seen[key] = res
        results.append(res)
        yield res


# -------------------------------------------------------------
# HTTP server
# -------------------------------------------------------------
class QwenPimHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        super().end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == '/api/stream_verify':
            self.handle_stream_verify(parsed)
            return
        if parsed.path == '/api/export_obsidian_audit':
            self.handle_export_obsidian_audit()
            return
        super().do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == '/api/save_feedback':
            self.handle_save_feedback()
            return
        self.send_error(404, "Endpoint not found")

    def _json(self, code, payload):
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode('utf-8'))

    def handle_save_feedback(self):
        try:
            length = int(self.headers.get('Content-Length', 0))
            payload = json.loads(self.rfile.read(length).decode('utf-8'))
            handle, url = payload.get('handle'), payload.get('url')
            if handle and url:
                save_human_override(handle, url, payload.get('position', 'extra'),
                                    bool(payload.get('is_valid', True)), payload.get('note', ''))
                self._json(200, {"success": True, "message": "Feedback saved to Knowledge Base"})
                return
        except Exception as e:
            print(f"Error in handle_save_feedback: {e}")
        self.send_error(400, "Invalid payload")

    def handle_export_obsidian_audit(self):
        try:
            results_data = load_json(OUTPUT_JSON, {})
            stamp = time.strftime('%Y%m%d_%H%M%S')
            audit_file = os.path.join(AUDIT_DIR, f"catalog_audit_{stamp}.md")
            latest_file = os.path.join(AUDIT_DIR, "catalog_audit_latest.md")

            counts = {"valid": 0, "invalid": 0, "review": 0, "error": 0}
            total = 0
            for p in results_data.values():
                for item in p.get('item_photos', []):
                    total += 1
                    counts[item.get('verdict') or ('valid' if item.get('is_valid') else 'invalid')] = \
                        counts.get(item.get('verdict') or ('valid' if item.get('is_valid') else 'invalid'), 0) + 1

            badge = {"valid": "✅ VALID", "invalid": "❌ INVALID", "review": "🟡 REVIEW", "error": "⚠️ ERROR"}
            lines = [
                "---", "type: Catalog Audit Report", f"generated_at: {time.strftime('%Y-%m-%d %H:%M:%S')}",
                f"total_products: {len(results_data)}", f"model: {QWEN_MODEL} (escalation: {ESCALATION_MODEL})", "---", "",
                "# Catalog Quality Control Audit Report", "", "## Summary",
                f"- **Products**: {len(results_data)}",
                f"- **Side photos**: {total}",
                f"- **Valid**: {counts['valid']} | **Invalid**: {counts['invalid']} | **Needs review**: {counts['review']} | **Errors**: {counts['error']}",
                "", "---", "", "## Product details",
            ]
            for handle, p in results_data.items():
                lines += [f"### [[{handle}]]", f"- **Hero Photo**: [Reference Image]({p.get('model_photo_url')})",
                          f"- **Audited At**: {p.get('checked_at')}", "",
                          "| Position | Verdict | Score | Reason | Image |", "| :--- | :--- | :--- | :--- | :--- |"]
                for item in p.get('item_photos', []):
                    v = item.get('verdict') or ('valid' if item.get('is_valid') else 'invalid')
                    reason = (item.get('reason') or '').replace('|', '/').replace('\n', ' ')
                    lines.append(f"| Pos #{item.get('position')} | {badge.get(v, v)} | {item.get('score', 0)}% | {reason} | [View]({item.get('url')}) |")
                lines.append("")
            content = "\n".join(lines)
            for path in (audit_file, latest_file):
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(content)
            self._json(200, {"success": True, "audit_path": audit_file, "latest_path": latest_file})
        except Exception as e:
            self.send_error(500, f"Error exporting audit: {e}")

    def send_sse(self, event, data):
        try:
            self.wfile.write(f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n".encode('utf-8'))
            self.wfile.flush()
        except Exception:
            pass

    def handle_stream_verify(self, parsed):
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Connection', 'keep-alive')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()

        if not DASHSCOPE_API_KEY:
            self.close_connection = True
            self.send_sse('complete', {'total_checked': 0, 'message': 'DASHSCOPE_API_KEY missing: set it in .env'})
            return

        target = urllib.parse.parse_qs(parsed.query).get('handle', [None])[0]
        products = load_products()
        check_list = [(target, products[target])] if target and target in products else list(products.items())
        total = sum(len(p.get('item_photos', [])) for _, p in check_list)
        overrides = load_human_overrides()
        cv_data = load_json(CV_RESULTS_JSON, {})
        results_data = load_json(OUTPUT_JSON, {})
        counts = {"valid": 0, "invalid": 0, "review": 0, "error": 0}
        checked = 0

        self.send_sse('start', {'total_photos': total, 'products_count': len(check_list)})
        for handle, prod in check_list:
            prod.setdefault('handle', handle)
            hero_url = (prod.get('model_photo') or {}).get('url')
            if not hero_url:
                continue
            prod_results = []

            def on_photo(pos, url, _h=handle):
                self.send_sse('checking', {'handle': _h, 'position': pos, 'current': checked + 1, 'total': total, 'url': url})

            for res in verify_product(prod, overrides, cv_data, on_photo=on_photo):
                checked += 1
                counts[res['verdict']] = counts.get(res['verdict'], 0) + 1
                prod_results.append(res)
                self.send_sse('photo_result', {'handle': handle, 'position': res['position'], 'result': res,
                                               'current': checked, 'total': total})

            results_data[handle] = {
                "title": prod.get('title', handle),
                "model_photo_url": hero_url,
                "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "engine": f"qwen-v2:{QWEN_MODEL}",
                "item_photos": prod_results,
            }
            save_json(OUTPUT_JSON, results_data)

        export_verified_csv(results_data)
        self.close_connection = True   # end the SSE stream cleanly (v1 left it hanging on keep-alive)
        self.send_sse('complete', {
            'total_checked': checked, 'counts': counts,
            'message': f"Done: {counts['valid']} valid, {counts['invalid']} invalid, "
                       f"{counts['review']} need review, {counts['error']} errors.",
        })


def export_verified_csv(results_data):
    try:
        if not os.path.exists(CSV_FILE):
            return
        with open(CSV_FILE, mode='r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            fieldnames = list(reader.fieldnames or [])
            for col in ['Is Side Image Invalid', 'AI Verdict', 'AI Match Score', 'AI Reason']:
                if col not in fieldnames:
                    fieldnames.append(col)
            rows = []
            for row in reader:
                handle = (row.get('Handle') or '').strip()
                pos = (row.get('Image Position') or '').strip()
                src = (row.get('Image Src') or '').strip()
                p_res = results_data.get(handle)
                if p_res and src and normalize_url(src) == normalize_url(p_res.get('model_photo_url')) and pos in ('1', ''):
                    row.update({'Is Side Image Invalid': 'NO', 'AI Verdict': 'hero', 'AI Match Score': '100%',
                                'AI Reason': 'Model Photo (Hero Reference)'})
                elif p_res:
                    items = p_res.get('item_photos', [])
                    m = next((i for i in items if src and normalize_url(i.get('url')) == normalize_url(src)
                              and str(i.get('position')) == str(pos)), None) \
                        or next((i for i in items if src and normalize_url(i.get('url')) == normalize_url(src)), None) \
                        or next((i for i in items if pos and str(i.get('position')) == str(pos)), None)
                    if m:
                        v = m.get('verdict') or ('valid' if m.get('is_valid') else 'invalid')
                        row['Is Side Image Invalid'] = {'valid': 'NO', 'invalid': 'YES'}.get(v, 'REVIEW')
                        row['AI Verdict'] = v
                        row['AI Match Score'] = f"{m.get('score')}%"
                        row['AI Reason'] = m.get('reason', '')
                rows.append(row)
        with open(OUTPUT_CSV, mode='w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
    except Exception as e:
        print(f"CSV Export Error: {e}")


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def run_server():
    httpd = ThreadingHTTPServer(('', PORT), QwenPimHandler)
    print(f"🚀 Qwen Vision Server v2 running at http://localhost:{PORT}/")
    print(f"   model={QWEN_MODEL} escalation={ESCALATION_MODEL if ENABLE_ESCALATION else 'off'}")
    print(f"   thresholds: valid>={VALID_CONF} invalid>={INVALID_CONF} attr>={ATTR_CONF}")
    print(f"   debug log: {os.path.abspath(DEBUG_LOG)}")
    if not DASHSCOPE_API_KEY:
        print("   ⚠️  DASHSCOPE_API_KEY is not set. Verification will not run.")
    httpd.serve_forever()


if __name__ == '__main__':
    run_server()
