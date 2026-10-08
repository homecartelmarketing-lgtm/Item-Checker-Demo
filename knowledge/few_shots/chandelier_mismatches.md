---
type: Verified Few-Shot Examples
purpose: High-Precision Qwen Vision Few-Shot Prompt Calibration
---

# Verified Few-Shot Contrast Cases

## Case 1: Rectangular Chandelier vs. Circular Variant (Contaminated Gallery)
- **Product Title**: `Andora | Chandelier` (Model `DD249863-1000长`)
- **Reference Hero Image**: Rectangular crystal crown chandelier (100cm x 40cm x 28cm) with **two hanging cords**.
- **Candidate Image A (Pos #5)**: Circular round chandelier mounted over sofa with **one central hanging cord**.
  - **Verdict**: `is_match = false` (MISMATCH, 100% confidence)
  - **Reasoning**: Image 1 shows a 100x40cm rectangular chandelier with two suspension cords, while Candidate Image A depicts a circular variant with a single central cord.
- **Candidate Image B (Pos #6)**: Circular round chandelier against dark wall with **one central cord**.
  - **Verdict**: `is_match = false` (MISMATCH, 98% confidence)
  - **Reasoning**: Geometric shape mismatch: elongated rectangular chandelier with dual cords vs round circular fixture with single suspension rod.
- **Candidate Image C (Pos #3)**: White background cut-out of the exact rectangular chandelier with dual cords and dimension text `100CM`.
  - **Verdict**: `is_match = true` (MATCH, 100% confidence)
- **Candidate Image D (Extra Shot 5)**: Styled dining room scene with the exact rectangular chandelier with dual cords.
  - **Verdict**: `is_match = true` (MATCH, 100% confidence)
- **Candidate Image E (Extra Shot 6)**: Close-up macro photo of K9 faceted crystal prisms.
  - **Verdict**: `is_match = true` (MATCH, 95% confidence)
