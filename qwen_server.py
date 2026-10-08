"""
Qwen Real-time Vision Web Server with Obsidian Knowledge Base & Active Learning
================================================================================
Runs a lightweight local web server with real-time Server-Sent Events (SSE)
to stream Qwen 3.8 Flash Vision photo verification directly to the browser.

Integrations:
- Obsidian-compatible Knowledge Base (knowledge/rules, knowledge/brands, knowledge/few_shots)
- Catalog Specifications Injection (Dimensions, Shape, Model, Material)
- Strict Confidence Policy (< 80% confidence -> Mismatch)
- Active Learning Human Override Loop (knowledge/feedback/overrides.json)
- Automated Markdown Audit Report Export (knowledge/audits/)
"""

import os
import sys
import json
import csv
import time
import urllib.parse
from http.server import SimpleHTTPRequestHandler, HTTPServer
from openai import OpenAI

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

PORT = 8089
CSV_FILE = "InvalidSideImages.csv"
PRELOADED_DATA = "preloaded_data.json"
OUTPUT_JSON = "ai_results.json"
OUTPUT_CSV = "InvalidSideImages_Verified.csv"
KNOWLEDGE_DIR = "knowledge"
FEEDBACK_FILE = os.path.join(KNOWLEDGE_DIR, "feedback", "overrides.json")
AUDIT_DIR = os.path.join(KNOWLEDGE_DIR, "audits")

# Ensure feedback and audit dirs exist
os.makedirs(os.path.join(KNOWLEDGE_DIR, "feedback"), exist_ok=True)
os.makedirs(AUDIT_DIR, exist_ok=True)

# Load environment variables from local .env if present
def _load_env_file():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
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

DASHSCOPE_API_KEY = os.environ.get("DASHSCOPE_API_KEY", "")

# Qwen Cloud Dashscope Client
QWEN_CLIENT = OpenAI(
    api_key=DASHSCOPE_API_KEY or "missing_key",
    base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
)

def load_human_overrides():
    if os.path.exists(FEEDBACK_FILE):
        try:
            with open(FEEDBACK_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get('overrides', {})
        except Exception as e:
            print(f"Error loading human overrides: {e}")
    return {}

def save_human_override(handle, url, position, is_valid, note=""):
    data = {"description": "Operator Manual Feedback & Correction Overrides", "overrides": {}}
    if os.path.exists(FEEDBACK_FILE):
        try:
            with open(FEEDBACK_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            pass

    key = f"{handle}::{url}"
    data.setdefault("overrides", {})[key] = {
        "handle": handle,
        "url": url,
        "position": position,
        "is_valid": is_valid,
        "note": note or ("Marked Valid by Operator" if is_valid else "Flagged Invalid by Operator"),
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
    }
    data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

    with open(FEEDBACK_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)

def load_products():
    """Loads products from preloaded_data.json if present, fallback to CSV."""
    if os.path.exists(PRELOADED_DATA):
        try:
            with open(PRELOADED_DATA, 'r', encoding='utf-8') as f:
                items = json.load(f)
                return {p['handle']: p for p in items}
        except Exception as e:
            print(f"Error reading {PRELOADED_DATA}: {e}")

    # Fallback to CSV parsing
    if not os.path.exists(CSV_FILE):
        return {}
    products = {}
    with open(CSV_FILE, mode='r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            handle = (row.get('Handle') or '').strip()
            if not handle:
                continue
            if handle not in products:
                products[handle] = {
                    'handle': handle,
                    'title': (row.get('Title') or handle).strip(),
                    'vendor': (row.get('Vendor') or '').strip(),
                    'type': (row.get('Type') or '').strip(),
                    'category': (row.get('Product Category') or '').strip(),
                    'specs': {},
                    'model_photo': None,
                    'item_photos': []
                }
            img_src = (row.get('Image Src') or '').strip()
            pos = (row.get('Image Position') or '').strip()
            if img_src:
                img_data = {'url': img_src, 'position': pos or 'extra', 'isValid': True}
                if products[handle]['model_photo'] is None:
                    products[handle]['model_photo'] = img_data
                else:
                    products[handle]['item_photos'].append(img_data)
    return products

def load_knowledge_context(product):
    """Loads relevant rules, brand profiles, and few-shots from the Obsidian Knowledge Base."""
    title = product.get('title', '')
    cat_type = (product.get('type') or product.get('category') or 'Chandelier').lower()
    vendor = (product.get('vendor') or '').lower()

    rules_text = ""

    # Category rules
    if 'pendant' in cat_type or 'pendant' in title.lower():
        rule_file = os.path.join(KNOWLEDGE_DIR, 'rules', 'pendant_lights.md')
    else:
        rule_file = os.path.join(KNOWLEDGE_DIR, 'rules', 'chandeliers.md')

    if os.path.exists(rule_file):
        with open(rule_file, 'r', encoding='utf-8') as f:
            rules_text += "\n\n[OBSIDIAN KNOWLEDGE BASE - CATEGORY RULES]:\n" + f.read().strip()

    # Brand conventions
    if 'huang' in vendor or 'huang' in title.lower():
        brand_file = os.path.join(KNOWLEDGE_DIR, 'brands', 'huanglilai.md')
        if os.path.exists(brand_file):
            with open(brand_file, 'r', encoding='utf-8') as f:
                rules_text += "\n\n[OBSIDIAN KNOWLEDGE BASE - BRAND PROFILE]:\n" + f.read().strip()

    # Few-shots
    few_shot_file = os.path.join(KNOWLEDGE_DIR, 'few_shots', 'chandelier_mismatches.md')
    if os.path.exists(few_shot_file):
        with open(few_shot_file, 'r', encoding='utf-8') as f:
            rules_text += "\n\n[OBSIDIAN KNOWLEDGE BASE - VERIFIED FEW-SHOT EXAMPLES]:\n" + f.read().strip()

    return rules_text

def build_qwen_prompt(product):
    """Constructs a high-precision prompt with injected specs and Obsidian KB rules."""
    specs = product.get('specs', {})
    dim_parts = [specs.get('length'), specs.get('width'), specs.get('height')]
    dims = " x ".join([p for p in dim_parts if p]) or specs.get('diameter') or 'N/A'

    specs_block = f"""PRODUCT CATALOG SPECIFICATIONS:
- Product Title: {product.get('title')}
- Expected Shape: {specs.get('shape') or 'See Hero Reference Photo'}
- Expected Dimensions: {dims}
- Model Code: {specs.get('model') or product.get('sku') or 'N/A'}
- Materials: {specs.get('material') or 'Metal / Crystal'}
- Vendor: {product.get('vendor') or 'N/A'}"""

    kb_block = load_knowledge_context(product)

    return f"""You are a senior Quality Control Inspector for an e-commerce home decor catalog.
Compare Image 1 (Model Hero Reference) and Image 2 (Candidate Side Image).
Determine if Image 2 depicts the EXACT same physical product design and variant as Image 1, or a mismatched/different variant.

{specs_block}
{kb_block}

EVALUATION RUBRIC:
1. Shape & Topology: Do Image 1 and Image 2 have the exact same 3D geometric shape (e.g., both rectangular/elongated vs circular/round)?
2. Suspension & Mounting: Do both fixtures share the exact same mounting structure (e.g. 2 suspension cords for rectangular vs 1 central cord for circular)?
3. Materials & Prisms: Do the hardware color and crystal prisms belong to this exact fixture?
4. Scene Type: Is Image 2 an isolated product cut-out, a styled lifestyle room scene of this exact model, or an authentic detail zoom?

STRICT ACCURACY POLICY:
- If confidence is < 80%, you MUST mark is_match as false.
- Rectangular chandeliers and Circular chandeliers are ALWAYS DIFFERENT VARIANTS (is_match: false).
- Single-head pendants and Multi-head cluster pendants are ALWAYS DIFFERENT VARIANTS (is_match: false).

Respond ONLY with valid JSON:
{{
  "shape_assessment": "Analysis of geometric shape (e.g. Rectangular vs Circular)",
  "suspension_assessment": "Analysis of cords/mounting (e.g. Dual cords vs Single cord)",
  "material_assessment": "Analysis of finishes and crystals",
  "scene_type": "product_cutout | lifestyle_room | detail_closeup",
  "is_match": true/false,
  "confidence": 0-100,
  "reason": "1-2 sentence concise explanation of decision"
}}"""

def load_critic_rubric():
    rubric_file = os.path.join(KNOWLEDGE_DIR, "rules", "critic_rubric.md")
    if os.path.exists(rubric_file):
        try:
            with open(rubric_file, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            pass
    return ""

def run_critic_verification(model_url, side_url, product, inspector_reason):
    """
    Pass 2: Devil's Advocate Critic Agent.
    Audits the candidate match to catch subtle variant mix-ups (cord count, shape, tiers, finish).
    """
    rubric = load_critic_rubric()
    specs = product.get("specs", {})
    expected_shape = specs.get("shape", "Unknown")
    dim_parts = [specs.get('length'), specs.get('width'), specs.get('height')]
    dims = " x ".join([p for p in dim_parts if p]) or specs.get('diameter') or 'N/A'

    critic_prompt = f"""You are the Strict Quality Control Critic (Devil's Advocate).
The primary inspector preliminarily proposed that Image 1 (Hero) and Image 2 (Candidate) depict the SAME variant.
Primary Inspector's reasoning: "{inspector_reason}"

Product Expected Specifications:
- Title: {product.get('title')}
- Expected Shape: {expected_shape}
- Dimensions: {dims}

{rubric}

CRITIC MANDATE:
Scrutinize Image 1 and Image 2 with extreme skepticism.
Your goal is to catch false positives. Actively search for reasons why Image 2 is a DIFFERENT VARIANT.
Specifically check:
1. Cable/Cord count: Does Image 1 have 2 hanging cords while Image 2 has 1 central cord?
2. Geometry: Is Image 1 elongated/rectangular while Image 2 is circular/round?
3. Tier count: Does Image 2 have more or fewer layers/tiers?
4. Fixture type: Is Image 2 a wall sconce or table lamp rather than the ceiling fixture?

Respond ONLY with valid JSON:
{{
  "veto": true/false,
  "critic_verdict": "VETO | AGREE",
  "discrepancy_found": "Exact structural difference found, or 'None - confirmed identical variant'",
  "critic_reason": "Concise 1-sentence explanation"
}}"""

    try:
        resp = QWEN_CLIENT.chat.completions.create(
            model="qwen3.8-flash",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": critic_prompt},
                    {"type": "image_url", "image_url": {"url": model_url}},
                    {"type": "image_url", "image_url": {"url": side_url}}
                ]
            }],
            response_format={"type": "json_object"}
        )
        return json.loads(resp.choices[0].message.content)
    except Exception as e:
        print(f"Critic Agent API Error: {e}")
        return {"veto": False, "critic_verdict": "AGREE", "discrepancy_found": "None", "critic_reason": ""}

class QwenPimHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        super().end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)

        # SSE Endpoint: Real-time verification stream
        if parsed.path == '/api/stream_verify':
            self.handle_stream_verify(parsed)
            return

        # Export Obsidian Markdown Audit
        if parsed.path == '/api/export_obsidian_audit':
            self.handle_export_obsidian_audit()
            return

        # Serve static files normally
        super().do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)

        # Save human operator override (Active Learning)
        if parsed.path == '/api/save_feedback':
            self.handle_save_feedback()
            return

        self.send_error(404, "Endpoint not found")

    def handle_save_feedback(self):
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            payload = json.loads(post_data.decode('utf-8'))

            handle = payload.get('handle')
            url = payload.get('url')
            position = payload.get('position', 'extra')
            is_valid = bool(payload.get('is_valid', True))
            note = payload.get('note', '')

            if handle and url:
                save_human_override(handle, url, position, is_valid, note)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"success": True, "message": "Feedback saved to Knowledge Base"}).encode('utf-8'))
                return
        except Exception as e:
            print(f"Error in handle_save_feedback: {e}")

        self.send_error(400, "Invalid payload")

    def handle_export_obsidian_audit(self):
        try:
            results_data = {}
            if os.path.exists(OUTPUT_JSON):
                with open(OUTPUT_JSON, 'r', encoding='utf-8') as f:
                    results_data = json.load(f)

            audit_file = os.path.join(AUDIT_DIR, f"catalog_audit_{time.strftime('%Y%m%d_%H%M%S')}.md")
            latest_file = os.path.join(AUDIT_DIR, "catalog_audit_latest.md")

            lines = [
                "---",
                "type: Catalog Audit Report",
                f"generated_at: {time.strftime('%Y-%m-%d %H:%M:%S')}",
                f"total_products: {len(results_data)}",
                "status: Verified via Qwen 3.8 Flash Vision",
                "---",
                "",
                "# Catalog Quality Control Audit Report",
                f"Generated on **{time.strftime('%Y-%m-%d %H:%M:%S')}**.",
                "",
                "## Summary Statistics"
            ]

            total_photos = 0
            total_mismatches = 0
            for handle, p in results_data.items():
                for item in p.get('item_photos', []):
                    total_photos += 1
                    if not item.get('is_valid'):
                        total_mismatches += 1

            lines.append(f"- **Total Products Audited**: {len(results_data)}")
            lines.append(f"- **Total Side Photos Analyzed**: {total_photos}")
            lines.append(f"- **Mismatched / Flagged Photos**: {total_mismatches}")
            lines.append(f"- **Valid Images Ratio**: {round((total_photos - total_mismatches) / max(1, total_photos) * 100, 1)}%")
            lines.append("")
            lines.append("---")
            lines.append("")
            lines.append("## Product-by-Product Verification Details")

            for handle, p in results_data.items():
                lines.append(f"### [[{handle}]]")
                lines.append(f"- **Hero Photo**: [Reference Image]({p.get('model_photo_url')})")
                lines.append(f"- **Audited At**: {p.get('checked_at')}")
                lines.append("")
                lines.append("| Position | Status | Score | AI Reasoning | Image Link |")
                lines.append("| :--- | :--- | :--- | :--- | :--- |")

                for item in p.get('item_photos', []):
                    status_badge = "✅ MATCH" if item.get('is_valid') else "❌ MISMATCH"
                    pos = f"Pos #{item.get('position')}"
                    score = f"{item.get('score', 0)}%"
                    reason = item.get('reason', '').replace('|', '/')
                    link = f"[View Image]({item.get('url')})"
                    lines.append(f"| {pos} | {status_badge} | {score} | {reason} | {link} |")
                lines.append("")

            content = "\n".join(lines)
            with open(audit_file, 'w', encoding='utf-8') as f:
                f.write(content)
            with open(latest_file, 'w', encoding='utf-8') as f:
                f.write(content)

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({
                "success": True,
                "audit_path": audit_file,
                "latest_path": latest_file
            }).encode('utf-8'))
        except Exception as e:
            self.send_error(500, f"Error exporting audit: {e}")

    def handle_stream_verify(self, parsed):
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Connection', 'keep-alive')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()

        # Parse query params if user wants to verify only current product
        query = urllib.parse.parse_qs(parsed.query)
        target_handle = query.get('handle', [None])[0]

        products = load_products()
        if target_handle and target_handle in products:
            check_list = [(target_handle, products[target_handle])]
        else:
            check_list = list(products.items())

        total_side_photos = sum(len(p.get('item_photos', [])) for _, p in check_list)
        checked_count = 0

        # Load active learning human overrides
        human_overrides = load_human_overrides()

        # Load existing results to update progressively
        results_data = {}
        if os.path.exists(OUTPUT_JSON):
            try:
                with open(OUTPUT_JSON, 'r', encoding='utf-8') as f:
                    results_data = json.load(f)
            except Exception:
                pass

        self.send_sse('start', {'total_photos': total_side_photos, 'products_count': len(check_list)})

        for p_idx, (handle, prod) in enumerate(check_list, 1):
            model_url = prod['model_photo']['url'] if prod.get('model_photo') else None
            if not model_url:
                continue

            # Build high-accuracy prompt with specs + Obsidian KB rules
            prompt = build_qwen_prompt(prod)
            prod_results = []

            for s_idx, side in enumerate(prod.get('item_photos', []), 1):
                side_url = side['url']
                pos = side.get('position', 'extra')
                checked_count += 1

                # Send "checking" status
                self.send_sse('checking', {
                    'handle': handle,
                    'position': pos,
                    'current': checked_count,
                    'total': total_side_photos,
                    'url': side_url
                })

                # Check if human operator previously verified/overrode this
                override_key = f"{handle}::{side_url}"
                if override_key in human_overrides:
                    ov = human_overrides[override_key]
                    is_valid = bool(ov.get('is_valid', True))
                    photo_res = {
                        "url": side_url,
                        "position": pos,
                        "score": 100.0,
                        "status": "match" if is_valid else "mismatch",
                        "is_valid": is_valid,
                        "reason": f"Operator Override ({ov.get('note', 'Verified')})",
                        "human_override": True
                    }
                else:
                    # Call Qwen 3.8 Flash Vision with injected context
                    try:
                        resp = QWEN_CLIENT.chat.completions.create(
                            model="qwen3.8-flash",
                            messages=[{
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": prompt},
                                    {"type": "image_url", "image_url": {"url": model_url}},
                                    {"type": "image_url", "image_url": {"url": side_url}}
                                ]
                            }],
                            response_format={"type": "json_object"}
                        )
                        qwen_eval = json.loads(resp.choices[0].message.content)
                        is_match = bool(qwen_eval.get('is_match', False))
                        confidence = float(qwen_eval.get('confidence', 80))
                        reason = str(qwen_eval.get('reason', 'Analyzed by Qwen Vision'))

                        critic_data = None
                        # Pass 2: If primary inspector proposed a match, trigger Strict Critic (Devil's Advocate)
                        if is_match and confidence >= 60.0:
                            self.send_sse('critic_checking', {
                                'handle': handle,
                                'position': pos,
                                'message': 'Running Pass 2: Strict Critic double-check...'
                            })
                            critic_data = run_critic_verification(model_url, side_url, prod, reason)
                            if critic_data.get('veto'):
                                is_match = False
                                confidence = min(confidence, 40.0)
                                disc = critic_data.get('discrepancy_found', 'Structural discrepancy')
                                reason = f"Vetoed by Critic: {disc} ({critic_data.get('critic_reason', '')})"

                        # STRICT 85% CONFIDENCE POLICY
                        if confidence < 85.0:
                            is_match = False
                            if "Vetoed" not in reason and "Strict" not in reason:
                                reason += " (Strict QC Policy: < 85% confidence)"

                        status = "match" if is_match else "mismatch"
                        photo_res = {
                            "url": side_url,
                            "position": pos,
                            "score": round(confidence, 1),
                            "status": status,
                            "is_valid": is_match,
                            "reason": reason,
                            "critic_vetoed": bool(critic_data and critic_data.get('veto')),
                            "critic_notes": critic_data.get('discrepancy_found', '') if critic_data else '',
                            "rubric": {
                                "shape": qwen_eval.get('shape_assessment', ''),
                                "suspension": qwen_eval.get('suspension_assessment', ''),
                                "material": qwen_eval.get('material_assessment', ''),
                                "scene": qwen_eval.get('scene_type', '')
                            }
                        }
                    except Exception as e:
                        photo_res = {
                            "url": side_url,
                            "position": pos,
                            "score": 50.0,
                            "status": "mismatch",
                            "is_valid": False,
                            "reason": f"API Error: {e}"
                        }

                prod_results.append(photo_res)

                # Send real-time result to browser
                self.send_sse('photo_result', {
                    'handle': handle,
                    'position': pos,
                    'result': photo_res,
                    'current': checked_count,
                    'total': total_side_photos
                })

            # Update results data progressively
            results_data[handle] = {
                "title": prod.get('title', handle),
                "model_photo_url": model_url,
                "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "item_photos": prod_results
            }

            with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
                json.dump(results_data, f, indent=2)

        # Export CSV
        self.export_verified_csv(results_data)

        self.send_sse('complete', {
            'total_checked': checked_count,
            'message': 'All photos checked with Qwen Vision & Obsidian KB!'
        })

    def send_sse(self, event, data):
        try:
            msg = f"event: {event}\ndata: {json.dumps(data)}\n\n"
            self.wfile.write(msg.encode('utf-8'))
            self.wfile.flush()
        except Exception:
            pass

    def export_verified_csv(self, results_data):
        try:
            if not os.path.exists(CSV_FILE):
                return
            with open(CSV_FILE, mode='r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                fieldnames = list(reader.fieldnames or [])
                for col in ['Is Side Image Invalid', 'AI Match Score', 'AI Reason']:
                    if col not in fieldnames:
                        fieldnames.append(col)

                rows = []
                for row in reader:
                    handle = (row.get('Handle') or '').strip()
                    pos = (row.get('Image Position') or '').strip()
                    src = (row.get('Image Src') or '').strip()
                    p_res = results_data.get(handle)

                    if pos == '1':
                        row['Is Side Image Invalid'] = 'NO'
                        row['AI Match Score'] = '100%'
                        row['AI Reason'] = 'Model Photo (Hero Reference)'
                    elif p_res:
                        match_item = None
                        if pos:
                            match_item = next((item for item in p_res.get('item_photos', []) if str(item.get('position')) == str(pos)), None)
                        if not match_item and src:
                            match_item = next((item for item in p_res.get('item_photos', []) if item.get('url') == src), None)

                        if match_item:
                            row['Is Side Image Invalid'] = 'NO' if match_item['is_valid'] else 'YES'
                            row['AI Match Score'] = f"{match_item['score']}%"
                            row['AI Reason'] = match_item.get('reason', '')
                    rows.append(row)

            with open(OUTPUT_CSV, mode='w', encoding='utf-8', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
        except Exception as e:
            print(f"CSV Export Error: {e}")

def run_server():
    server_address = ('', PORT)
    httpd = HTTPServer(server_address, QwenPimHandler)
    print(f"🚀 Qwen Vision Server with Obsidian KB running at http://localhost:{PORT}/")
    print(f"⚡ Streaming SSE: http://localhost:{PORT}/api/stream_verify")
    print(f"📁 Obsidian Knowledge Base: {os.path.abspath(KNOWLEDGE_DIR)}")
    httpd.serve_forever()

if __name__ == '__main__':
    run_server()
