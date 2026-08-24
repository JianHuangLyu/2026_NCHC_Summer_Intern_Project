Skill_Name: Soft_Tissue_and_Mesenchymal_Patterns

Description:
This new conditional skill describes soft-tissue and mesenchymal cellular patterns, matrix relationships, and vascular context without assigning lineage or tumour type.

Task:
Characterize visible mesenchymal components and their arrangement using descriptive morphology.

Assess:
- Spindle, epithelioid, round, adipocytic-appearing, giant, multinucleated, clear, granular, or mixed cell populations
- Fascicular, storiform, patternless, haphazard, nested, lobulated, plexiform, palisaded, perivascular, zonal, or biphasic patterns
- Alternating cellularity, collagenous, hyalinized, myxoid, myxocollagenous, chondroid-appearing, osteoid-like, or mixed matrix
- Mature adipose tissue, vacuolated cells, wavy or elongated nuclei, branching or slit-like vascular channels, and entrapped native structures

Rules:
1. Apply Global_Rules.md.
2. Do not infer adipocytic, fibroblastic, smooth-muscle, nerve-sheath, vascular, or other differentiation from morphology alone.
3. Use descriptive `-appearing` terms when lineage or material is unconfirmed.
4. Do not convert fascicles, pleomorphism, myxoid matrix, or vascular patterns into a lesion name.
5. Do not assign FNCLCC or any other histological grade.

Output:
- Mesenchymal_Component
- Predominant_Cell_Morphology
- Dominant_Mesenchymal_Pattern
- Secondary_Patterns
- Matrix_Relationship
- Cellularity_and_Heterogeneity
- Adipose_or_Vacuolated_Component
- Vascular_Context
- Entrapped_Native_Structures
- Assessability
- Limitations
