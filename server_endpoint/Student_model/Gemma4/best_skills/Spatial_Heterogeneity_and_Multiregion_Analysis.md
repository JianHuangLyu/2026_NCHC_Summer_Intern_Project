Skill_Name: Spatial_Heterogeneity_and_Multiregion_Analysis

Description:
This new conditional skill compares multiple ROIs or spatially separable regions and records feature distribution without extrapolating unsampled tissue.

Task:
Map dominant, secondary, focal, and region-specific morphological findings across the supplied spatial coverage.

Assess:
- ROI identifiers, coordinates, areas, scale, and tissue coverage
- Regional cellularity, architecture, matrix, necrosis, mitoses, inflammation, hemorrhage, and deposits
- Dominant and minor patterns, transitions, gradients, hotspots, and inter-ROI variability
- Distance to visible border, lumen, vessel, or other annotated structure when measurable
- Sampling adequacy and unsampled-area limitation

Rules:
1. Apply Global_Rules.md.
2. Use only supplied regions and coordinates; do not invent spatial relationships.
3. Distinguish measured distances and areas from visual estimates.
4. Do not claim whole-slide heterogeneity unless slide coverage is adequate and explicitly available.
5. Preserve per-ROI results before producing cross-region summaries.

Output:
- Region_List
- Region_Level_Findings
- Dominant_and_Minor_Patterns
- Pattern_Transitions
- Feature_Hotspots
- Interregion_Heterogeneity
- Spatial_Relationships
- Coverage_and_Sampling_Adequacy
- Assessability
- Limitations
