"""Image helpers for the Item Checker (visual evidence only).

- fetch_image(): download once, cache on disk in cache_images/
- contain() / pad_square(): resize WITHOUT stretching the product
- dhash_similarity(): near-duplicate detection
- dino_cosine(): optional DINOv2 similarity (only if torch is installed)
- to_data_url(): send the already-downloaded image to Qwen as base64,
  so supplier CDNs that block hotlinking do not cause random errors.

Nothing here reads text, OCR, filenames or metadata.
"""
from __future__ import annotations

import base64
import hashlib
import io
import os
import sys
import threading
import urllib.request

from PIL import Image, ImageOps

from checker_utils import dhash_pil, hamming, normalize_url

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "cache_images")
os.makedirs(CACHE_DIR, exist_ok=True)

_MEM: dict[str, Image.Image] = {}
_MEM_LOCK = threading.Lock()


def _cache_path(url: str) -> str:
    return os.path.join(CACHE_DIR, hashlib.sha1(normalize_url(url).encode("utf-8")).hexdigest() + ".img")


def fetch_image(url: str, timeout: int = 30, retries: int = 3) -> Image.Image:
    """Returns an RGB PIL image. Local paths work too. Raises on failure."""
    if not url.startswith(("http://", "https://")):
        return ImageOps.exif_transpose(Image.open(url)).convert("RGB")
    key = normalize_url(url)
    with _MEM_LOCK:
        if key in _MEM:
            return _MEM[key]
    path = _cache_path(url)
    data = None
    if os.path.exists(path):
        with open(path, "rb") as f:
            data = f.read()
    else:
        last = None
        for _ in range(retries):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": ""})
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    data = resp.read()
                break
            except Exception as exc:  # network hiccup, retry
                last = exc
        if data is None:
            raise RuntimeError(f"download failed: {last}")
        with open(path, "wb") as f:
            f.write(data)
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    with _MEM_LOCK:
        if len(_MEM) > 400:
            _MEM.clear()
        _MEM[key] = img
    return img


def contain(img: Image.Image, size: int = 1024) -> Image.Image:
    """Shrink to fit inside size x size, keeping proportions."""
    return ImageOps.contain(img, (size, size), method=Image.Resampling.LANCZOS)


def pad_square(img: Image.Image, size: int = 224, fill=(255, 255, 255)) -> Image.Image:
    """Letterbox to a square. Never stretches: a rectangular chandelier stays rectangular."""
    return ImageOps.pad(img, (size, size), method=Image.Resampling.LANCZOS, color=fill)


def to_data_url(img: Image.Image, max_side: int = 1024, quality: int = 90) -> str:
    buf = io.BytesIO()
    contain(img, max_side).save(buf, format="JPEG", quality=quality)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def dhash_similarity(a: Image.Image, b: Image.Image) -> tuple[float, int]:
    """(similarity 0..1, hamming distance 0..64)."""
    dist = hamming(dhash_pil(pad_square(a, 256)), dhash_pil(pad_square(b, 256)))
    return round(1.0 - dist / 64.0, 4), dist


# ---------------- optional DINOv2 ----------------
_DINO = {"model": None, "transform": None, "tried": False}
_DINO_LOCK = threading.Lock()


def init_dino() -> bool:
    if _DINO["tried"]:
        return _DINO["model"] is not None
    _DINO["tried"] = True
    if os.getenv("ENABLE_DINO", "1") != "1":
        return False
    try:
        import torch  # noqa: F401
        import torchvision.transforms as T
        import torch.hub
        name = os.getenv("DINO_MODEL", "dinov2_vitb14")
        _DINO["model"] = torch.hub.load("facebookresearch/dinov2", name).eval()
        # Image is already padded to 224x224, so no Resize here (Resize((224,224)) used to squash it).
        _DINO["transform"] = T.Compose([
            T.ToTensor(), T.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
        ])
        return True
    except Exception as exc:
        print(f"[dino] disabled: {exc}", file=sys.stderr)
        _DINO["model"] = None
        return False


def dino_cosine(a: Image.Image, b: Image.Image) -> float | None:
    if _DINO["model"] is None:
        return None
    try:
        import torch
        with _DINO_LOCK, torch.no_grad():
            t = _DINO["transform"]
            ea = _DINO["model"](t(pad_square(a)).unsqueeze(0))
            eb = _DINO["model"](t(pad_square(b)).unsqueeze(0))
            ea = ea / ea.norm(dim=-1, keepdim=True)
            eb = eb / eb.norm(dim=-1, keepdim=True)
            return round(float((ea @ eb.T).item()), 4)
    except Exception as exc:
        print(f"[dino] failed: {exc}", file=sys.stderr)
        return None
