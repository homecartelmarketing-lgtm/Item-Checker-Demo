---
category: General Lighting
type: Universal E-Commerce Standards
author: Catalog PIM QC Team
version: 1.0.0
---

# Universal E-Commerce Lighting Inspection Standards

## 1. Primary Hero Reference (Row 1)
- The Hero image (Position 1) is the ground-truth contract with the customer.
- What the customer sees in Image 1 defines what they expect to receive upon purchase.

## 2. Supplier Gallery Contamination
- Chinese factories often dump 20-30 images containing 5 different variants (round, oval, square, wall sconce, floor lamp) into a single product listing.
- **Rule**: If a side image shows a **Wall Sconce** or **Table Lamp** while the hero is a **Ceiling Chandelier**, mark as **MISMATCH**.

## 3. Strict Confidence Policy
- If overall match confidence is below **80%**, default to **MISMATCH (Invalid Side Image)**.
- It is far safer to exclude a borderline photo than to deliver an incorrect physical variant to a customer.
