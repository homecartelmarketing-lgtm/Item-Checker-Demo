"""Visual-only product matcher.

Reference image is compared with each candidate image using only visible product
appearance. OCR, metadata, filenames, URLs and background text are never used
as match evidence.

Usage:
  python visual_only_matcher.py --reference hero.jpg --candidates side1.jpg side2.jpg
  python visual_only_matcher.py --reference https://... --candidates https://...

Requires: pillow, imagehash. Optional: torch, torchvision, transformers/openai.
Set DASHSCOPE_API_KEY to enable Qwen verification. Without it, the deterministic
pHash/DINO stage still returns a reviewable visual score.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
import urllib.request
from pathlib import Path

from PIL import Image, ImageOps

try:
    import imagehash
except ImportError:
    imagehash = None


def load_image(value: str) -> Image.Image:
    if value.startswith(("http://", "https://")):
        req = urllib.request.Request(value, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as response:
            return Image.open(io.BytesIO(response.read())).convert("RGB")
    return Image.open(value).convert("RGB")


def normalized(img: Image.Image, size: int = 512) -> Image.Image:
    """Keep the product proportions. Do not squash rectangular products."""
    img = ImageOps.exif_transpose(img).convert("RGB")
    return ImageOps.contain(img, (size, size), method=Image.Resampling.LANCZOS)


def dhash_score(a: Image.Image, b: Image.Image) -> float | None:
    if imagehash is None:
        return None
    distance = imagehash.dhash(normalized(a)) - imagehash.dhash(normalized(b))
    return round(max(0.0, 1.0 - distance / 64.0), 4)


def dino_score(a: Image.Image, b: Image.Image) -> float | None:
    """Optional semantic visual score. Returns None when torch/model is unavailable."""
    try:
        import torch
        import torchvision.transforms as T
        model = dino_score.model
        transform = dino_score.transform
        with torch.no_grad():
            ea = model(transform(normalized(a)).unsqueeze(0))
            eb = model(transform(normalized(b)).unsqueeze(0))
            ea = ea / ea.norm(dim=-1, keepdim=True)
            eb = eb / eb.norm(dim=-1, keepdim=True)
            return round(float((ea @ eb.T).item()), 4)
    except Exception as exc:
        if os.getenv("DEBUG_MATCHER"):
            print(f"DINO unavailable: {exc}", file=sys.stderr)
        return None


def init_dino() -> None:
    try:
        import torch
        import torchvision.transforms as T
        name = os.getenv("DINO_MODEL", "dinov2_vitb14")
        dino_score.model = torch.hub.load("facebookresearch/dinov2", name).eval()
        dino_score.transform = T.Compose([
            T.Resize((224, 224)), T.ToTensor(),
            T.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
        ])
    except Exception:
        dino_score.model = None
        dino_score.transform = None


def qwen_verify(reference: str, candidate: str) -> dict | None:
    key = os.getenv("DASHSCOPE_API_KEY")
    if not key or not reference.startswith(("http://", "https://")) or not candidate.startswith(("http://", "https://")):
        return None
    try:
        from openai import OpenAI
        client = OpenAI(
            api_key=key,
            base_url=os.getenv("DASHSCOPE_BASE_URL", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"),
        )
        prompt = """Image 1 is the reference product. Image 2 is the candidate.
Decide whether they show the SAME physical product design and variant.
Compare ONLY visible physical appearance: silhouette, proportions, geometry,
cords/rods, arms, tiers, heads, materials and distinctive details.
Ignore all OCR, printed text, model codes, dimensions, metadata, brand, URL,
filename and background. Different angle, crop, lighting, room scene or close-up
is not automatically different. If visibility is insufficient, return unsure.
Return JSON only: {\"verdict\":\"same_variant|different_variant|unsure\",\"confidence\":0,\"reason\":\"short visual reason\"}"""
        response = client.chat.completions.create(
            model=os.getenv("QWEN_MODEL", "qwen3.8-flash"),
            temperature=0,
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": reference}},
                {"type": "image_url", "image_url": {"url": candidate}},
            ]}],
        )
        return json.loads(response.choices[0].message.content)
    except Exception as exc:
        return {"verdict": "error", "confidence": 0, "reason": str(exc)}


def classify(dino: float | None, dhash: float | None, qwen: dict | None) -> str:
    if qwen:
        verdict = qwen.get("verdict")
        confidence = float(qwen.get("confidence", 0) or 0)
        if verdict == "same_variant" and confidence >= 90:
            return "valid"
        if verdict == "different_variant" and confidence >= 85:
            return "invalid"
        if verdict == "error":
            return "error"
    if dhash is not None and dhash >= 0.97:
        return "valid"
    if dino is not None and dino >= float(os.getenv("DINO_VALID", "0.80")):
        return "review"
    return "review"


def compare(reference: str, candidate: str) -> dict:
    ref_img, cand_img = load_image(reference), load_image(candidate)
    dhash = dhash_score(ref_img, cand_img)
    dino = dino_score(ref_img, cand_img) if getattr(dino_score, "model", None) is not None else None
    qwen = qwen_verify(reference, candidate)
    verdict = classify(dino, dhash, qwen)
    return {
        "reference": reference, "candidate": candidate, "verdict": verdict,
        "score": qwen.get("confidence") if qwen else (dino * 100 if dino is not None else None),
        "dino_cosine": dino, "dhash_similarity": dhash, "qwen": qwen,
        "evidence_policy": "visual appearance only; text, metadata and background ignored",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", required=True)
    parser.add_argument("--candidates", nargs="+", required=True)
    parser.add_argument("--no-dino", action="store_true")
    args = parser.parse_args()
    if not args.no_dino:
        init_dino()
    print(json.dumps([compare(args.reference, candidate) for candidate in args.candidates], indent=2))


if __name__ == "__main__":
    main()
