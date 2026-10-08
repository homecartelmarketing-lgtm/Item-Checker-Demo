"""
AI Auto-Checker Pipeline (CV) v2
================================
Offline computer-vision evidence for product photo verification.
This is a PRE-FILTER / evidence provider, not the final judge: results go to
cv_results.json and qwen_server.py reads them as extra evidence.

Fixes vs v1:
- Letterbox (pad) images instead of squashing to squares, so rectangular vs
  round shapes survive into DINOv2 / LightGlue.
- Raw DINOv2 cosine similarity (v1 used (sim+1)/2, which made unrelated images score ~60).
- LightGlue matches are geometrically verified with RANSAC; we score INLIERS,
  not raw matches (crystal prisms create lots of repetitive false matches).
- Background removed on BOTH the side photo and the room candidate crop.
- Full room image is only used when segmentation finds nothing.
- Hero = Image Position 1; side photos identical to the hero are skipped.
- 3 outcomes: valid / invalid / review. Thresholds are env-tunable and MUST be
  calibrated with eval.py on labelled data.
- Writes cv_results.json (no longer overwrites the Qwen ai_results.json).
"""

import os
import sys
import io
import csv
import re
import time
import urllib.request

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

import cv2
import numpy as np
from PIL import Image
import torch
import torchvision.transforms as T

from checker_utils import normalize_url, save_json, products_from_csv

# -------------------------------------------------------------
# Configuration (uncalibrated defaults: tune with eval.py)
# -------------------------------------------------------------
CSV_FILE = os.environ.get("CV_INPUT_CSV", "InvalidSideImages.csv")
OUTPUT_JSON = os.environ.get("CV_OUTPUT_JSON", "cv_results.json")
OUTPUT_CSV = os.environ.get("CV_OUTPUT_CSV", "InvalidSideImages_CV.csv")
CACHE_DIR = "cache_images"
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")
DINO_MODEL = os.environ.get("DINO_MODEL", "dinov2_vitb14")   # vits14 is faster, vitb14 more discriminative

DINO_VALID = float(os.environ.get("CV_DINO_VALID", 0.80))      # cosine >= -> supports valid
DINO_INVALID = float(os.environ.get("CV_DINO_INVALID", 0.55))  # cosine <  -> supports invalid
INLIERS_VALID = int(os.environ.get("CV_INLIERS_VALID", 15))    # RANSAC inliers >= -> supports valid
AR_CONFLICT_RATIO = float(os.environ.get("CV_AR_CONFLICT", 1.6))  # silhouette aspect-ratio ratio -> shape conflict

print("=" * 60)
print("[CV] Auto-Checker v2 starting")
print(f"[CV] Device: {DEVICE} ({torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU'})")
print("=" * 60)
os.makedirs(CACHE_DIR, exist_ok=True)


# -------------------------------------------------------------
# Image helpers
# -------------------------------------------------------------
def sanitize_filename(name):
    return re.sub(r'[^a-zA-Z0-9_\-\.]', '_', name or 'item').strip('_')


def get_cached_image(url, prefix="img"):
    if not url:
        return None, None
    url_clean = url.split('?')[0]
    filename = f"{prefix}_{sanitize_filename(os.path.basename(url_clean))}"
    if not filename.lower().endswith(('.jpg', '.jpeg', '.png', '.webp')):
        filename += ".jpg"
    local_path = os.path.join(CACHE_DIR, filename)
    if os.path.exists(local_path) and os.path.getsize(local_path) > 1000:
        try:
            return Image.open(local_path).convert("RGB"), local_path
        except Exception:
            pass
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = resp.read()
            with open(local_path, 'wb') as f:
                f.write(data)
            return Image.open(io.BytesIO(data)).convert("RGB"), local_path
        except Exception as e:
            if attempt == 2:
                print(f"  [!] Failed to download: {url[:60]}... ({e})")
            time.sleep(1.5 * (attempt + 1))
    return None, None


def letterbox(pil_img, size, fill=(255, 255, 255)):
    """Resize keeping aspect ratio, pad to a square. Never distorts shape."""
    w, h = pil_img.size
    scale = size / max(w, h)
    nw, nh = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    resized = pil_img.resize((nw, nh), Image.BICUBIC)
    canvas = Image.new("RGB", (size, size), fill)
    canvas.paste(resized, ((size - nw) // 2, (size - nh) // 2))
    return canvas


# -------------------------------------------------------------
# Stage 1: rembg
# -------------------------------------------------------------
import rembg

print("[+] Initializing rembg (u2net)...", flush=True)
rembg_session = rembg.new_session("u2net")


def remove_background(pil_img):
    """Returns (cutout on white, aspect_ratio of silhouette)."""
    try:
        buf = io.BytesIO()
        pil_img.save(buf, format='PNG')
        rgba = Image.open(io.BytesIO(rembg.remove(buf.getvalue(), session=rembg_session))).convert("RGBA")
        bbox = rgba.getbbox()
        if bbox:
            rgba = rgba.crop(bbox)
        white = Image.new("RGB", rgba.size, (255, 255, 255))
        white.paste(rgba, mask=rgba.split()[3])
        w, h = white.size
        return white, w / max(1, h)
    except Exception:
        w, h = pil_img.size
        return pil_img, w / max(1, h)


# -------------------------------------------------------------
# Stage 2: FastSAM candidates
# -------------------------------------------------------------
from ultralytics import FastSAM

print("[+] Loading FastSAM...")
sam_model = FastSAM('FastSAM-s.pt')


def extract_room_candidates(room_img_path, room_pil):
    W, H = room_pil.size
    total = W * H
    boxes_out = []
    try:
        results = sam_model(room_img_path, device=str(DEVICE), retina_masks=True, conf=0.25, verbose=False)
        if results and results[0].boxes is not None:
            for b in results[0].boxes.xyxy.cpu().numpy():
                x1, y1, x2, y2 = map(int, b)
                area = (x2 - x1) * (y2 - y1)
                if 0.015 * total <= area <= 0.85 * total:
                    boxes_out.append([x1, y1, x2, y2])
    except Exception as e:
        print(f"    [SAM warning] {e}")
    if not boxes_out:
        boxes_out = [[0, 0, W, H]]   # fallback only

    candidates = []
    for box in boxes_out:
        crop = room_pil.crop(tuple(box))
        clean, ar = remove_background(crop)   # same preprocessing as the side photo
        candidates.append({"box": box, "crop": clean, "ar": ar})
    return candidates


# -------------------------------------------------------------
# Stage 3: DINOv2
# -------------------------------------------------------------
print(f"[+] Loading DINOv2 ({DINO_MODEL})...")
dinov2 = torch.hub.load('facebookresearch/dinov2', DINO_MODEL).to(DEVICE).eval()
dino_norm = T.Compose([T.ToTensor(), T.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])])


def get_dino_embedding(pil_img):
    with torch.no_grad():
        t = dino_norm(letterbox(pil_img, 224)).unsqueeze(0).to(DEVICE)
        emb = dinov2(t)
        return emb / emb.norm(dim=-1, keepdim=True)


# -------------------------------------------------------------
# Stage 4: LightGlue + ALIKED + RANSAC
# -------------------------------------------------------------
print("[+] Loading LightGlue & ALIKED...")
from lightglue import ALIKED, LightGlue
from lightglue.utils import rbd

aliked_extractor = ALIKED(max_num_keypoints=1024).eval().to(DEVICE)
lightglue_matcher = LightGlue(features='aliked').eval().to(DEVICE)


def match_lightglue(pil1, pil2):
    """Returns (raw_matches, ransac_inliers)."""
    try:
        t1 = T.ToTensor()(letterbox(pil1, 512)).unsqueeze(0).to(DEVICE)
        t2 = T.ToTensor()(letterbox(pil2, 512)).unsqueeze(0).to(DEVICE)
        with torch.no_grad():
            f1 = aliked_extractor.extract(t1)
            f2 = aliked_extractor.extract(t2)
            m = lightglue_matcher({'image0': f1, 'image1': f2})
        f1, f2, m = [rbd(x) for x in (f1, f2, m)]
        idx = m['matches'].cpu().numpy()
        raw = len(idx)
        if raw < 8:
            return raw, 0
        p0 = f1['keypoints'].cpu().numpy()[idx[:, 0]]
        p1 = f2['keypoints'].cpu().numpy()[idx[:, 1]]
        method = getattr(cv2, "USAC_MAGSAC", cv2.RANSAC)
        _, mask = cv2.findHomography(p0, p1, method, 6.0)
        inliers = int(mask.sum()) if mask is not None else 0
        return raw, inliers
    except Exception as e:
        print(f"    [LightGlue warning] {e}")
        return 0, 0


# -------------------------------------------------------------
# Combined evidence
# -------------------------------------------------------------
def verify_side_photo(side_pil, room_candidates):
    clean_product, ar_prod = remove_background(side_pil)
    prod_emb = get_dino_embedding(clean_product)

    best = None
    for cand in room_candidates:
        cos = float(torch.mm(prod_emb, get_dino_embedding(cand["crop"]).T).item())
        raw, inl = match_lightglue(clean_product, cand["crop"])
        ar_ratio = max(ar_prod, cand["ar"]) / max(1e-6, min(ar_prod, cand["ar"]))
        # rank candidates by evidence strength, not by an inflated blended score
        rank = cos + min(inl, 60) / 120.0
        if best is None or rank > best["rank"]:
            best = dict(rank=rank, cos=cos, raw=raw, inl=inl, ar_ratio=ar_ratio, box=cand["box"])

    shape_conflict = best["ar_ratio"] >= AR_CONFLICT_RATIO and best["inl"] < INLIERS_VALID
    if best["cos"] >= DINO_VALID and best["inl"] >= INLIERS_VALID and not shape_conflict:
        verdict = "valid"
    elif best["cos"] < DINO_INVALID or (shape_conflict and best["cos"] < DINO_VALID):
        verdict = "invalid"
    else:
        verdict = "review"

    return {
        "score": round(max(0.0, best["cos"]) * 100, 1),
        "dinov2_score": round(max(0.0, best["cos"]) * 100, 1),   # raw cosine x100 (UI field)
        "dino_cosine": round(best["cos"], 4),
        "lightglue_matches": int(best["inl"]),                    # UI shows inliers now
        "lightglue_raw_matches": int(best["raw"]),
        "ransac_inliers": int(best["inl"]),
        "aspect_ratio_ratio": round(best["ar_ratio"], 2),
        "cv_shape_conflict": bool(shape_conflict),
        "verdict": verdict,
        "status": "match" if verdict == "valid" else "mismatch",
        "is_valid": verdict == "valid",
        "needs_review": verdict == "review",
        "box": best["box"],
    }


# -------------------------------------------------------------
# Main
# -------------------------------------------------------------
def main():
    if not os.path.exists(CSV_FILE):
        print(f"Error: {CSV_FILE} not found.")
        return
    products = products_from_csv(CSV_FILE)
    print(f"\nFound {len(products)} products in {CSV_FILE}.")
    results = {}
    counts = {"valid": 0, "invalid": 0, "review": 0, "skipped": 0}
    start = time.time()

    for p_idx, (handle, prod) in enumerate(products.items(), 1):
        print(f"\n[{p_idx}/{len(products)}] {prod['title']}")
        hero = prod.get('model_photo')
        if not hero:
            print("  [!] No hero photo. Skipping.")
            continue
        room_pil, room_path = get_cached_image(hero['url'], prefix=f"model_{p_idx}")
        if not room_pil:
            print("  [!] Could not load hero photo.")
            continue
        cands = extract_room_candidates(room_path, room_pil)
        print(f"  [SAM] {len(cands)} candidate regions")

        hero_key = normalize_url(hero['url'])
        seen = {}
        out = []
        for s_idx, side in enumerate(prod['item_photos'], 1):
            key = normalize_url(side['url'])
            if key == hero_key:
                res = {"verdict": "valid", "status": "match", "is_valid": True, "score": 100.0,
                       "duplicate_of_hero": True, "reason": "Same image as hero"}
                counts["skipped"] += 1
            elif key in seen:
                res = dict(seen[key], duplicate_of_position=seen[key].get("position"))
                counts["skipped"] += 1
            else:
                side_pil, _ = get_cached_image(side['url'], prefix=f"side_{p_idx}_{s_idx}")
                if not side_pil:
                    continue
                res = verify_side_photo(side_pil, cands)
                counts[res["verdict"]] += 1
            res = dict(res, url=side['url'], position=side['position'])
            seen.setdefault(key, res)
            out.append(res)
            print(f"    Pos {side['position']}: {res['verdict'].upper()} "
                  f"(cos {res.get('dino_cosine', '-')}, inliers {res.get('ransac_inliers', '-')}, "
                  f"AR ratio {res.get('aspect_ratio_ratio', '-')})", flush=True)

        results[handle] = {"title": prod['title'], "model_photo_url": hero['url'],
                           "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"), "engine": "cv-v2",
                           "item_photos": out}
        save_json(OUTPUT_JSON, results)

    # Annotated CSV (separate file, never overwrites the Qwen output)
    with open(CSV_FILE, mode='r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        fields = list(reader.fieldnames or []) + [c for c in ('CV Verdict', 'CV Cosine', 'CV Inliers') if c not in (reader.fieldnames or [])]
        rows = []
        for row in reader:
            p = results.get((row.get('Handle') or '').strip())
            src = normalize_url((row.get('Image Src') or '').strip())
            if p:
                m = next((i for i in p['item_photos'] if normalize_url(i['url']) == src), None)
                if src == normalize_url(p['model_photo_url']):
                    row['CV Verdict'] = 'hero'
                elif m:
                    row['CV Verdict'] = m.get('verdict')
                    row['CV Cosine'] = m.get('dino_cosine', '')
                    row['CV Inliers'] = m.get('ransac_inliers', '')
            rows.append(row)
    with open(OUTPUT_CSV, mode='w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    print("\n" + "=" * 60)
    print(f"CV CHECK COMPLETE in {round(time.time() - start, 1)}s")
    print(f"Valid {counts['valid']} | Invalid {counts['invalid']} | Review {counts['review']} | Skipped dup/hero {counts['skipped']}")
    print(f"JSON: {os.path.abspath(OUTPUT_JSON)}\nCSV:  {os.path.abspath(OUTPUT_CSV)}")
    print("Thresholds are uncalibrated defaults. Run eval.py with labels to tune them.")
    print("=" * 60)


if __name__ == '__main__':
    main()
