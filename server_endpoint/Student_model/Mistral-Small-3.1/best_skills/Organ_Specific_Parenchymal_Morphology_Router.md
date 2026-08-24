Skill_Name: Organ_Specific_Parenchymal_Morphology_Router

Description:
This new conditional router selects an explicitly supplied organ-specific morphology module. It does not infer organ identity or manufacture organ-specific rules.

Task:
Verify organ context, locate a corresponding approved module, and record whether organ-specific analysis can be performed.

Assess:
- Explicit organ or anatomical-site metadata
- Presence of relevant organ-specific parenchyma
- Availability and version of an approved organ module
- Compatibility of stain, specimen type, scale, and tissue representation
- Required fields missing for organ-specific analysis

Rules:
1. Apply Global_Rules.md.
2. Never infer the organ from morphology for the purpose of activating a diagnostic or organ-specific rule set.
3. Never fabricate an unavailable organ module.
4. If the organ is known but the module is unavailable, set dependent fields to `Unavailable` and continue with general morphology modules.
5. Organ-specific modules must remain non-diagnostic unless a separately authorized diagnostic workflow is supplied.

Output:
- Supplied_Organ_or_Site
- Organ_Context_Status
- Relevant_Parenchyma_Present
- Selected_Organ_Module
- Organ_Module_Version
- Organ_Module_Loading_Status
- Missing_Requirements
- Effect_on_Analysis
- Limitations
