# Product Catalog Validation Knowledge Base (Obsidian Vault)

This vault houses the quality control rules, category rubrics, supplier model patterns, and verified few-shot contrast examples used by the AI Catalog Item Checker.

## Vault Directory Structure

- [[rules/chandeliers|rules/chandeliers.md]]: Rules for chandeliers (shape topology, suspension cords, tier structures).
- [[rules/pendant_lights|rules/pendant_lights.md]]: Rules for pendant lights (single drop vs multi-head canopy).
- [[rules/general_lighting|rules/general_lighting.md]]: General e-commerce image validation guidelines (room scenes, close-ups, dimension sheets).
- [[brands/huanglilai|brands/huanglilai.md]]: Brand profiles and model numbering conventions (e.g., Huanglilai DD249863 series).
- [[few_shots/chandelier_mismatches|few_shots/chandelier_mismatches.md]]: Verified few-shot contrast cases (e.g., Rectangular 100x40cm vs Circular 60cm).
- `feedback/overrides.json`: Active learning log of human operator manual overrides.
- `audits/`: Generated inspection audit reports.

---
*Created for automated PIM Quality Control with Qwen 3.8 Flash Vision.*
