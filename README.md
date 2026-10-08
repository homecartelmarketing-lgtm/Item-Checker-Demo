# Item Checker Demo | Akeneo PIM Product Photo Verification

An AI-powered quality control and audit platform designed for e-commerce catalog operations (chandeliers, pendant lights, furniture, and home decor). It cross-references lifestyle/room model photos against product side photos to automatically detect mismatches, wrong variants, or invalid side angles.

---

## 🚀 Key Features

- **Interactive Web PIM Dashboard (`index.html`)**
  - Rich Akeneo-inspired design with dark/light themes.
  - Status filters: *All*, *Valid*, *Invalid / Mismatch*, and *Needs Review*.
  - Side-by-side visual comparison with synchronized zoom inspection.
  - Instant search across product handles, titles, SKUs, and categories.
  - Operator manual review modal with human-override feedback.
  - One-click Batch ZIP export of categorized product imagery.

- **Qwen Vision AI Verification Engine (`qwen_server.py`)**
  - Real-time Server-Sent Events (SSE) streaming verification powered by Qwen 3.8 Flash Vision (`dashscope`).
  - Catalog specifications injection (dimensions, shape, tier count, finishes, materials).
  - Strict confidence scoring policy (< 80% confidence flagged for manual review).

- **Obsidian-Compatible Knowledge Base (`knowledge/`)**
  - **Category Rules (`knowledge/rules/`)**: Rule rubrics for chandeliers, pendant lights, and general lighting.
  - **Brand Profiles (`knowledge/brands/`)**: Manufacturer traits and packaging characteristics (e.g. Huanglilai).
  - **Few-Shot Examples (`knowledge/few_shots/`)**: Documented mismatch patterns for visual prompt context.
  - **Active Learning Overrides (`knowledge/feedback/`)**: Stores operator override decisions to refine AI prompts.
  - **Audit Exports (`knowledge/audits/`)**: Generates structured Markdown audit reports.

- **Offline Computer Vision Pipeline (`auto_checker.py`)**
  - Background removal via `rembg`.
  - Object segmentation via `FastSAM` (`FastSAM-s.pt`).
  - Semantic feature embedding via `DINOv2`.
  - Geometric keypoint & texture matching via `LightGlue` + `ALIKED`.

---

## 📁 Repository Structure

```text
├── index.html                    # Web UI dashboard
├── app.js                        # Client application logic & Obsidian KB integration
├── style.css                     # UI styling (dark/light theme, modern PIM layout)
├── data.js                       # Pre-compiled dataset bundle for offline demo
├── preloaded_data.json           # Catalog items and image metadata
├── qwen_server.py                # Local Python web server & Qwen Vision AI backend
├── auto_checker.py               # Deep learning computer vision pipeline
├── export_images.py              # CLI batch image downloader
├── InvalidSideImages.csv         # Input CSV dataset
├── InvalidSideImages_Verified.csv# AI verified results CSV
├── ai_results.json               # Cached AI verification outputs
├── FastSAM-s.pt                  # FastSAM lightweight model weights
├── cache_images/                 # Cached sample catalog images
├── knowledge/                    # Obsidian Knowledge Base
│   ├── rules/                    # Lighting verification rubrics
│   ├── brands/                   # Vendor & brand specifications
│   ├── few_shots/                # Historical mismatch examples
│   ├── feedback/                 # Human operator feedback & overrides
│   └── audits/                   # Generated markdown audit reports
├── .env.example                  # Environment variable configuration template
└── .gitignore                    # Git ignore file
```

---

## ⚡ Quick Start

### Option 1: Standalone Web Interface (No Server Required)
Simply open `index.html` in any modern web browser:
```bash
# On Windows
start index.html
```
The application will load the pre-computed catalog dataset (`data.js` / `preloaded_data.json`) and display the full verification interface.

---

### Option 2: Live AI Server (Qwen Vision Backend)

1. **Install Python dependencies:**
   ```bash
   pip install openai
   ```
   *(For running the offline computer vision pipeline in `auto_checker.py`, also install `torch torchvision opencv-python pillow rembg ultralytics`)*

2. **Configure Environment Variables:**
   Copy `.env.example` to `.env` and set your Alibaba Cloud DashScope API key:
   ```bash
   cp .env.example .env
   ```
   Edit `.env`:
   ```ini
   DASHSCOPE_API_KEY=your_dashscope_api_key_here
   ```

3. **Start the local server:**
   ```bash
   python qwen_server.py
   ```

4. **Open in browser:**
   Navigate to [http://localhost:8089](http://localhost:8089)

---

## 🛠️ CLI Utilities

- **Export and Download Images:**
  ```bash
  python export_images.py
  ```
- **Run Local CV Matching Pipeline:**
  ```bash
  python auto_checker.py
  ```

---

## 📄 License
MIT License.
