Skill_Name: Specimen_Image_and_Stain_Context

Description:
This skill records supplied specimen, image, stain, scale, and ROI metadata needed to interpret what can and cannot be assessed. It does not infer missing metadata from morphology.

Task:
Record the analysis context and mark missing contextual fields explicitly.

Assess:
- Image level: WSI, region, ROI, or patch
- Stain or modality as explicitly supplied
- Specimen type and tissue processing as explicitly supplied
- Organ or anatomical site as explicitly supplied
- Scanner magnification, microns per pixel, and image dimensions
- ROI identifier, coordinates, dimensions, annotation type, and tissue area
- Case, specimen, block, slide, and ROI hierarchy
- Single-region versus multiregion input

Rules:
1. Apply Global_Rules.md.
2. Do not infer organ, stain, specimen type, processing method, magnification, or clinical context from appearance alone.
3. Use `Unknown` for metadata not supplied and `N/A` only when the field is genuinely inapplicable.
4. Keep identifiers and contextual metadata separate from morphological findings.
5. Do not expose patient-identifying information beyond what is necessary for the requested analysis.

Output:
- Image_Level
- Stain_or_Modality
- Specimen_Type
- Tissue_Processing
- Organ_or_Anatomical_Site
- Scanner_and_Scale
- Image_Dimensions
- ROI_Metadata
- Specimen_Hierarchy
- Region_Count
- Missing_Context
- Limitations
