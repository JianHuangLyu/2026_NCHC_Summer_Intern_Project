Skill_Name: Tissue_Border_Interface_and_Growth_Pattern

Description:
This new skill describes visible borders and interfaces between a focal tissue region and adjacent tissue without diagnosing invasion.

Task:
Evaluate whether a relevant border is visible and describe its morphology and relationship to adjacent structures.

Assess:
- Border visibility and completeness
- Circumscribed, pushing-appearing, permeative-appearing, infiltrative-appearing, irregular, or indeterminate interface
- Capsule-like tissue and completeness
- Entrapment or interdigitation of pre-existing structures
- Perivascular, perineural, intravascular, mucosal, serosal, or compartmental relationship when directly visible
- Distance to image or ROI edge

Rules:
1. Apply Global_Rules.md.
2. Use `N/A` when no tissue interface is present and `Not_Evaluable` when the interface is truncated or poorly represented.
3. Use `-appearing` for growth-pattern descriptions and never convert them into invasive or non-invasive classification.
4. Do not infer a capsule or complete circumscription from a partial edge.
5. Do not infer relationships outside the ROI.

Output:
- Border_Presence
- Border_Completeness
- Border_Morphology
- Capsule_Like_Tissue
- Interface_with_Adjacent_Tissue
- Entrapment_or_Interdigitation
- Visible_Structural_Relationships
- ROI_Edge_Limitation
- Assessability
- Limitations
