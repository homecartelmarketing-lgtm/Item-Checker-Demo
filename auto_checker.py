"""
AI Auto-Checker Pipeline
========================
Automated Verification for Product Photos vs Room/Model Scenes.
Components:
  1. rembg: Isolates catalog product / removes background
  2. SAM / FastSAM: Segments & crops candidate objects in room photo
  3. DINOv2: Computes global semantic / visual similarity
  4. LightGlue + ALIKED: Matches geometric patterns, textures, and keypoints
"""

import os
import sys
import csv
import json
import re
import urllib.request
import io
import time
from pathlib import Path

# Fix Windows console encoding for UTF-8
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

# -------------------------------------------------------------
# Configuration
# -------------------------------------------------------------
CSV_FILE = "InvalidSideImages.csv"
OUTPUT_JSON = "ai_results.json"
CACHE_DIR = "cache_images"
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")
MATCH_THRESHOLD = 60.0  # Percentage threshold for PASS/FAIL

print("=" * 60)
print("[AI] AI Auto-Checker Pipeline Starting...")
print(f"[AI] Device: {DEVICE} ({torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU'})")
print("=" * 60)

os.makedirs(CACHE_DIR, exist_ok=True)

# -------------------------------------------------------------
# 1. Download & Image Cache Helper
# -------------------------------------------------------------
def sanitize_filename(name):
    return re.sub(r'[^a-zA-Z0-9_\-\.]', '_', name or 'item').strip('_')

def get_cached_image(url, prefix="img"):
    """Downloads image with caching to avoid re-downloading."""
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

    try:
        req = urllib.request.Request(
            url,
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = resp.read()
            with open(local_path, 'wb') as f:
                f.write(data)
            return Image.open(io.BytesIO(data)).convert("RGB"), local_path
    except Exception as e:
        print(f"  [!] Failed to download: {url[:50]}... ({e})")
        return None, None

# -------------------------------------------------------------
# 2. Pipeline Stage 1: rembg (Cutout Background Removal)
# -------------------------------------------------------------
import rembg

print("[+] Initializing rembg (u2net model)...", flush=True)
rembg_session = rembg.new_session("u2net")

def remove_background(pil_img):
    """Uses rembg to remove background and tightly crop to bounding box."""
    try:
        img_bytes = io.BytesIO()
        pil_img.save(img_bytes, format='PNG')
        output_bytes = rembg.remove(img_bytes.getvalue(), session=rembg_session)
        rgba = Image.open(io.BytesIO(output_bytes)).convert("RGBA")

        # Tightly crop to non-transparent bbox
        bbox = rgba.getbbox()
        if bbox:
            rgba = rgba.crop(bbox)

        # Composite over clean white background for matchers
        white_bg = Image.new("RGB", rgba.size, (255, 255, 255))
        white_bg.paste(rgba, mask=rgba.split()[3])
        return white_bg
    except Exception as e:
        return pil_img

# -------------------------------------------------------------
# 3. Pipeline Stage 2: SAM / FastSAM (Candidate Room Crops)
# -------------------------------------------------------------
from ultralytics import FastSAM

print("[+] Loading FastSAM segmentation model...")
sam_model = FastSAM('FastSAM-s.pt')

def extract_room_candidates(room_img_path, room_pil):
    """Extract candidate object crops from room scene photo."""
    W, H = room_pil.size
    total_area = W * H
    candidates = []

    # Always include the entire image as baseline candidate
    candidates.append({"box": [0, 0, W, H], "crop": room_pil})

    try:
        results = sam_model(room_img_path, device=str(DEVICE), retina_masks=True, conf=0.25, verbose=False)
        if results and len(results) > 0 and results[0].boxes is not None:
            boxes = results[0].boxes.xyxy.cpu().numpy()
            for b in boxes:
                x1, y1, x2, y2 = map(int, b)
                area = (x2 - x1) * (y2 - y1)
                # Keep objects between 1.5% and 85% of scene
                if 0.015 * total_area <= area <= 0.85 * total_area:
                    crop = room_pil.crop((x1, y1, x2, y2))
                    candidates.append({
                        "box": [x1, y1, x2, y2],
                        "crop": crop
                    })
    except Exception as e:
        print(f"    [SAM warning] {e}")

    return candidates

# -------------------------------------------------------------
# 4. Pipeline Stage 3: DINOv2 (Semantic Visual Similarity)
# -------------------------------------------------------------
print("[+] Loading DINOv2 Vision Transformer...")
dinov2 = torch.hub.load('facebookresearch/dinov2', 'dinov2_vits14').to(DEVICE).eval()

dino_transform = T.Compose([
    T.Resize((224, 224)),
    T.ToTensor(),
    T.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])
])

def get_dino_embedding(pil_img):
    with torch.no_grad():
        tensor = dino_transform(pil_img).unsqueeze(0).to(DEVICE)
        emb = dinov2(tensor)
        emb = emb / emb.norm(dim=-1, keepdim=True)
    return emb

# -------------------------------------------------------------
# 5. Pipeline Stage 4: LightGlue + ALIKED (Keypoints & Pattern)
# -------------------------------------------------------------
print("[+] Loading LightGlue & ALIKED feature matchers...")
from lightglue import ALIKED, LightGlue
from lightglue.utils import rbd

aliked_extractor = ALIKED(max_num_keypoints=512).eval().to(DEVICE)
lightglue_matcher = LightGlue(features='aliked').eval().to(DEVICE)

def match_lightglue(pil_img1, pil_img2):
    """Calculates geometric correspondence between product cutout and room crop."""
    try:
        img1 = pil_img1.resize((384, 384))
        img2 = pil_img2.resize((384, 384))

        t1 = T.ToTensor()(img1).unsqueeze(0).to(DEVICE)
        t2 = T.ToTensor()(img2).unsqueeze(0).to(DEVICE)

        with torch.no_grad():
            feat1 = aliked_extractor.extract(t1)
            feat2 = aliked_extractor.extract(t2)
            matches = lightglue_matcher({'image0': feat1, 'image1': feat2})

            feat1, feat2, matches = [rbd(x) for x in [feat1, feat2, matches]]

        num_matches = len(matches['matches'])
        scores = matches['scores']
        conf = scores.mean().item() if num_matches > 0 else 0.0

        return num_matches, conf
    except Exception as e:
        return 0, 0.0

MATCH_THRESHOLD = 65.0  # Percentage threshold for PASS/FAIL
OUTPUT_CSV = "InvalidSideImages_Verified.csv"

# -------------------------------------------------------------
# 6. Combined Matching Logic
# -------------------------------------------------------------
def verify_side_photo(side_pil, room_pil, room_candidates):
    """
    Runs the 4-stage pipeline:
    1. rembg cutout of side photo
    2. compares against room candidates
    3. evaluates shape aspect ratio consistency (e.g. round vs rectangular)
    4. combines DINOv2 and LightGlue
    """
    clean_product = remove_background(side_pil)
    prod_emb = get_dino_embedding(clean_product)

    # Isolated product aspect ratio
    w_prod, h_prod = clean_product.size
    ar_prod = w_prod / max(1, h_prod)

    best_score = 0.0
    best_candidate = None
    best_dino = 0.0
    best_lg_matches = 0

    for cand in room_candidates:
        crop = cand["crop"]
        crop_emb = get_dino_embedding(crop)

        # DINOv2 Cosine Similarity (rescaled from [-1, 1] to [0, 100])
        sim = torch.mm(prod_emb, crop_emb.T).item()
        dino_score = max(0.0, min(100.0, ((sim + 1.0) / 2.0) * 100.0))

        # LightGlue keypoint matching
        lg_matches, lg_conf = match_lightglue(clean_product, crop)
        # Normalize LightGlue: 25+ matches = high confidence
        lg_score = min(100.0, (lg_matches / 25.0) * 80.0 + lg_conf * 20.0)

        # Candidate aspect ratio check
        w_crop, h_crop = crop.size
        ar_crop = w_crop / max(1, h_crop)
        
        # Shape / Aspect Ratio Consistency:
        # Severe penalty if one is elongated/rectangular (e.g. AR > 2.0) and one is circular/square (e.g. AR ~ 1.0)
        ar_diff = abs(ar_crop - ar_prod)
        shape_multiplier = 1.0
        if lg_matches < 20 and ar_diff > 0.8:
            # Significant shape discrepancy with low keypoint match
            shape_multiplier = max(0.4, 1.0 - (ar_diff * 0.35))

        # Combined Confidence: 55% DINOv2 + 45% LightGlue multiplied by shape consistency
        combined = (0.55 * dino_score + 0.45 * lg_score) * shape_multiplier

        if combined > best_score:
            best_score = combined
            best_candidate = cand
            best_dino = dino_score
            best_lg_matches = lg_matches

    is_valid = best_score >= MATCH_THRESHOLD
    status = "match" if is_valid else "mismatch"

    return {
        "score": round(best_score, 1),
        "dinov2_score": round(best_dino, 1),
        "lightglue_matches": int(best_lg_matches),
        "status": status,
        "is_valid": is_valid,
        "box": best_candidate["box"] if best_candidate else None
    }

# -------------------------------------------------------------
# 7. Main Execution Flow
# -------------------------------------------------------------
def main():
    if not os.path.exists(CSV_FILE):
        print(f"Error: {CSV_FILE} not found.")
        return

    # Parse CSV into products
    products = {}
    with open(CSV_FILE, mode='r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            handle = (row.get('Handle') or '').strip()
            if not handle:
                continue
            if handle not in products:
                products[handle] = {
                    'title': (row.get('Title') or handle).strip(),
                    'model_photo': None,
                    'item_photos': []
                }
            img_src = (row.get('Image Src') or '').strip()
            pos = (row.get('Image Position') or '').strip()
            if img_src:
                img_data = {'url': img_src, 'position': pos or 'extra'}
                if products[handle]['model_photo'] is None:
                    products[handle]['model_photo'] = img_data
                else:
                    products[handle]['item_photos'].append(img_data)

    print(f"\nFound {len(products)} products in {CSV_FILE}.")
    results_data = {}

    total_photos_checked = 0
    total_valid = 0
    total_flagged = 0

    start_time = time.time()

    for p_idx, (handle, prod) in enumerate(products.items(), 1):
        print(f"\n[{p_idx}/{len(products)}] Processing: {prod['title']}")
        
        if not prod['model_photo']:
            print("  [!] No Model Photo found. Skipping.")
            continue

        model_url = prod['model_photo']['url']
        room_pil, room_path = get_cached_image(model_url, prefix=f"model_{p_idx}")
        if not room_pil:
            print("  [!] Could not load room photo.")
            continue

        print("  [SAM] Segmenting candidate items in Room Scene...")
        room_candidates = extract_room_candidates(room_path, room_pil)
        print(f"  [SAM] Found {len(room_candidates)} candidate regions.")

        prod_results = []

        for s_idx, side in enumerate(prod['item_photos'], 1):
            side_url = side['url']
            side_pil, _ = get_cached_image(side_url, prefix=f"side_{p_idx}_{s_idx}")
            if not side_pil:
                continue

            res = verify_side_photo(side_pil, room_pil, room_candidates)
            res['url'] = side_url
            res['position'] = side['position']
            prod_results.append(res)

            total_photos_checked += 1
            if res['is_valid']:
                total_valid += 1
                badge = "[MATCH]"
            else:
                total_flagged += 1
                badge = "[MISMATCH]"

            print(f"    Shot #{s_idx} (Pos {side['position']}): {badge} {res['score']}% (DINO: {res['dinov2_score']}%, LG: {res['lightglue_matches']} pts)", flush=True)

        results_data[handle] = {
            "title": prod['title'],
            "model_photo_url": model_url,
            "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "item_photos": prod_results
        }

        # Save progressively to JSON so UI updates live
        with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
            json.dump(results_data, f, indent=2)

    # Export verified CSV with annotations
    try:
        updated_rows = []
        with open(CSV_FILE, mode='r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            fieldnames = list(reader.fieldnames or [])
            if 'Is Side Image Invalid' not in fieldnames:
                fieldnames.append('Is Side Image Invalid')
            if 'AI Match Score' not in fieldnames:
                fieldnames.append('AI Match Score')

            for row in reader:
                handle = (row.get('Handle') or '').strip()
                pos = (row.get('Image Position') or '').strip()
                p_res = results_data.get(handle)
                if p_res and pos:
                    if pos == '1':
                        row['Is Side Image Invalid'] = 'NO'
                        row['AI Match Score'] = '100% (Model Hero)'
                    else:
                        match_item = next((item for item in p_res.get('item_photos', []) if str(item.get('position')) == str(pos)), None)
                        if match_item:
                            row['Is Side Image Invalid'] = 'NO' if match_item['is_valid'] else 'YES'
                            row['AI Match Score'] = f"{match_item['score']}%"
                updated_rows.append(row)

        with open(OUTPUT_CSV, mode='w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(updated_rows)
        print(f"Verified CSV exported to: {os.path.abspath(OUTPUT_CSV)}")
    except Exception as e:
        print(f"[!] Warning: Could not export CSV: {e}")

    elapsed = round(time.time() - start_time, 1)

    print("\n" + "=" * 60)
    print("AI AUTO-CHECK COMPLETE!")
    print(f"Time Elapsed: {elapsed} seconds")
    print(f"Total Side Photos Checked: {total_photos_checked}")
    print(f"Verified Valid: {total_valid} | Flagged Invalid: {total_flagged}")
    print(f"JSON Results: {os.path.abspath(OUTPUT_JSON)}")
    print(f"CSV Results:  {os.path.abspath(OUTPUT_CSV)}")
    print("=" * 60)

if __name__ == '__main__':
    main()
