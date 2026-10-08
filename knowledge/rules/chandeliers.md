---
category: Chandelier
type: Quality Control Inspection Rulebook
author: Catalog PIM QC Team
version: 2.0.0
strictness: Ultra-Strict Enterprise Level
min_confidence_match: 85%
---

# Chandelier Quality Control Inspection Rules (Ultra-Strict Edition)

Chandeliers are high-value lighting fixtures frequently scraped with multi-variant supplier galleries. Suppliers on Taobao/Tmall/1688 routinely combine all shapes, sizes, and mounting styles of a collection onto a single product page.

## 1. Shape & Geometric Topology (Strict Disqualification)
- **Rectangular / Elongated vs. Circular / Round**:
  - If Image 1 is **Rectangular/Linear/Oval** and Image 2 is **Circular/Round**, this is a **STRICT MISMATCH (is_match = false)**.
  - If Image 1 is **Circular/Round** and Image 2 is **Rectangular/Linear**, this is a **STRICT MISMATCH (is_match = false)**.
  - Shared crystal style, gold metal plating, or similar lighting ambiance **CANNOT** override shape mismatch.

## 2. Suspension Structure (Mounting Assessment)
- **Dual Suspension Cords/Chains**:
  - Rectangular chandeliers require **two distinct ceiling attachment points / suspension cords** to balance weight.
- **Single Central Cord/Rod**:
  - Circular or square chandeliers use **one central suspension cord or rod**.
  - A side photo showing a single central cord when the hero photo has dual cords is a **STRICT VETO (Disqualification)**.

## 3. Tier Count & Layering
- Single-tier fixture vs Multi-tier (2-tier, 3-tier) fixture = **STRICT MISMATCH**.

## 4. OCR Model Suffix Cross-Check
- Chinese suffix `长` (Cháng): Rectangular/linear fixture.
- Chinese suffix `圆` (Yuán): Circular/round fixture.
- Chinese prefix/suffix `壁` (Bì): Wall sconce (instant mismatch for chandeliers).
- If hero has `长` and candidate side image text says `圆` or `壁`, mark as **STRICT MISMATCH**.

## 5. Strict 85% Confidence Policy
- If match confidence is **< 85%**, mark as **MISMATCH (Invalid Side Image)** by default.
- In e-commerce catalog publishing, false positives ruin customer trust and cause costly returns. We prioritize **100% precision over recall**.
