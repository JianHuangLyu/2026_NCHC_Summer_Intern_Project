Skill_Name: Epithelial_and_Glandular_Architecture

Description:
This new conditional skill evaluates epithelial, mucosal, glandular, and ductal organization when such structures are visible.

Task:
Describe epithelial layering, polarity, luminal organization, and epithelial-specific architectural patterns without diagnosing a lesion.

Assess:
- Surface, glandular, ductal, acinar, tubular, solid, papillary, micropapillary, cribriform, comedo-like, trabecular, nested, and single-cell patterns
- One-to-several layers, stratification, bridging, tufting, budding, fusion, and fenestration
- Cell polarity, basal-apical orientation, luminal organization, and basement-membrane relationship when visible
- Keratinization, mucin-like material, and epithelial cohesion

Rules:
1. Apply Global_Rules.md.
2. Load only when relevant epithelial structures are visible.
3. Do not classify tissue as benign, atypical, dysplastic, in situ, invasive, or malignant.
4. Do not infer an intact or breached basement membrane unless directly resolved.
5. Multiple patterns require independent visible evidence.

Output:
- Epithelial_Component
- Dominant_Epithelial_Pattern
- Secondary_Epithelial_Patterns
- Epithelial_Layering
- Polarity_and_Orientation
- Bridging_Tufting_or_Budding
- Glandular_or_Ductal_Organization
- Luminal_Organization
- Structural_Fusion_or_Complexity
- Basement_Membrane_Assessability
- Assessability
- Limitations
