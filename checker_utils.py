"""
Shared helpers for the Item Checker pipelines (Qwen server, CV checker, eval).
Pure Python + Pillow only, so every script can import it.
"""

import io
import os
import re
import json
import urllib.request

_CROP_RE = re.compile(r"~crop,[^~]*~")
_SIZE_SUFFIX_RE = re.compile(r"(\.(?:jpe?g|png|webp))_\d+x\d+.*$", re.IGNORECASE)


def normalize_url(url):
    """Canonical form of an image URL so the same picture is recognised even when
    the CDN adds query strings, crop directives or thumbnail suffixes."""
    if not url:
        return ""
    u = url.strip().split("?")[0].split("#")[0]
    u = _CROP_RE.sub("", u)
    u = _SIZE_SUFFIX_RE.sub(r"\1", u)
    u = re.sub(r"^https?://", "", u, flags=re.IGNORECASE)
    return u.lower()


def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[!] Could not read {path}: {e}")
    return default


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


# -------------------------------------------------------------
# Perceptual hash (dHash) for near-duplicate detection
# -------------------------------------------------------------
_DHASH_CACHE = {}


def dhash_pil(img, size=8):
    from PIL import Image
    g = img.convert("L").resize((size + 1, size), Image.LANCZOS)
    px = list(g.getdata())
    bits = 0
    for row in range(size):
        for col in range(size):
            left = px[row * (size + 1) + col]
            right = px[row * (size + 1) + col + 1]
            bits = (bits << 1) | (1 if left > right else 0)
    return bits


def dhash_url(url, timeout=15):
    """Downloads an image and returns its 64-bit dHash, or None on failure."""
    if not url:
        return None
    key = normalize_url(url)
    if key in _DHASH_CACHE:
        return _DHASH_CACHE[key]
    h = None
    try:
        from PIL import Image
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
        h = dhash_pil(Image.open(io.BytesIO(data)))
    except Exception as e:
        print(f"  [dhash] skipped {url[:60]}... ({e})")
    _DHASH_CACHE[key] = h
    return h


def hamming(a, b):
    if a is None or b is None:
        return None
    return bin(a ^ b).count("1")


# -------------------------------------------------------------
# CSV -> product structure (same columns the web UI reads)
# -------------------------------------------------------------
def specs_from_row(row):
    g = lambda *keys: next(((row.get(k) or "").strip() for k in keys if (row.get(k) or "").strip()), "")
    return {
        "material": g("CSVEDITOR_Material", "PRODINF_Material"),
        "length": g("CSVEDITOR_Length"),
        "width": g("CSVEDITOR_Width"),
        "height": g("CSVEDITOR_Height"),
        "diameter": g("CSVEDITOR_Diameter"),
        "shape": g("CSVEDITOR_Shape"),
        "color": g("CSVEDITOR_Color"),
        "model": g("PRODINF_Model"),
        "brand": g("PRODINF_Brand"),
        "light_source": g("CSVEDITOR_LightSource"),
    }


def products_from_csv(csv_path):
    """Parses the Shopify-style CSV. Hero = Image Position 1 when present,
    otherwise the first image row (old behaviour)."""
    import csv
    products = {}
    rows_by_handle = {}
    with open(csv_path, mode="r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            handle = (row.get("Handle") or "").strip()
            if not handle:
                continue
            rows_by_handle.setdefault(handle, []).append(row)

    for handle, rows in rows_by_handle.items():
        first = rows[0]
        prod = {
            "handle": handle,
            "title": (first.get("Title") or handle).strip(),
            "sku": (first.get("Variant SKU") or first.get("SKU") or "").strip(),
            "vendor": (first.get("Vendor") or "").strip(),
            "type": (first.get("Type") or "").strip(),
            "category": (first.get("Product Category") or "").strip(),
            "specs": specs_from_row(first),
            "model_photo": None,
            "item_photos": [],
        }
        images = []
        for row in rows:
            src = (row.get("Image Src") or "").strip()
            if src:
                images.append({"url": src, "position": (row.get("Image Position") or "").strip() or "extra"})
        hero_idx = next((i for i, im in enumerate(images) if im["position"] == "1"), 0 if images else None)
        if hero_idx is not None:
            prod["model_photo"] = images[hero_idx]
            prod["item_photos"] = [im for i, im in enumerate(images) if i != hero_idx]
        products[handle] = prod
    return products
