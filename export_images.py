"""
Export Images CLI Script
Downloads Model Photos and Item Side Photos from InvalidSideImages.csv directly to local folders.
"""

import os
import csv
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

CSV_FILE = "InvalidSideImages.csv"
OUTPUT_DIR = "downloaded_images"

def sanitize_filename(name):
    return re.sub(r'[^a-zA-Z0-9_\-\.]', '_', name or 'item').strip('_')

def download_file(url, target_path):
    try:
        req = urllib.request.Request(
            url,
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Referer': ''
            }
        )
        with urllib.request.urlopen(req, timeout=20) as response:
            data = response.read()
            with open(target_path, 'wb') as f:
                f.write(data)
        return True, url, target_path, None
    except Exception as e:
        return False, url, target_path, str(e)

def main():
    if not os.path.exists(CSV_FILE):
        print(f"Error: {CSV_FILE} not found.")
        return

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    # 1. Parse CSV and group products
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

    print(f"Found {len(products)} products in {CSV_FILE}.")

    # 2. Build download task list
    tasks = []
    for handle, prod in products.items():
        folder_name = sanitize_filename(handle)
        prod_dir = os.path.join(OUTPUT_DIR, folder_name)
        os.makedirs(prod_dir, exist_ok=True)

        # Model Photo (Row 1)
        if prod['model_photo']:
            url = prod['model_photo']['url']
            ext = os.path.splitext(url.split('?')[0])[1] or '.jpg'
            target = os.path.join(prod_dir, f"01_MODEL_PHOTO{ext}")
            tasks.append((url, target))

        # Item Photos (Row 2+)
        for i, item in enumerate(prod['item_photos']):
            url = item['url']
            ext = os.path.splitext(url.split('?')[0])[1] or '.jpg'
            pos = item['position']
            target = os.path.join(prod_dir, f"02_SIDE_POS_{pos}_{i+1}{ext}")
            tasks.append((url, target))

    print(f"Downloading {len(tasks)} images using ThreadPoolExecutor...")

    # 3. Concurrent download
    success_count = 0
    fail_count = 0

    with ThreadPoolExecutor(max_workers=8) as executor:
        future_map = {executor.submit(download_file, url, path): (url, path) for url, path in tasks}
        for future in as_completed(future_map):
            success, url, target_path, err = future.result()
            if success:
                success_count += 1
                print(f"[OK] {os.path.basename(target_path)}")
            else:
                fail_count += 1
                print(f"[FAIL] {os.path.basename(target_path)} -> {err}")

    print("\n" + "=" * 50)
    print(f"Download Summary:")
    print(f"Total: {len(tasks)} | Succeeded: {success_count} | Failed: {fail_count}")
    print(f"Files saved in: {os.path.abspath(OUTPUT_DIR)}")
    print("=" * 50)

if __name__ == '__main__':
    main()
