Skill_Name: Pathology_Image_Quality_and_Structured_Morphology_Reporting

Description:
This revised skill first evaluates image quality and later integrates only the outputs actually produced by successfully loaded required and applicable conditional modules into a non-diagnostic report.

Task:
Assess image quality, track module applicability and availability, detect conflicts, and generate the final structured morphology report.

Assess:
- Focus, resolution, magnification adequacy, stain quality, color balance, over- or understaining
- Folding, tearing, chatter, sectioning, compression, crushing, retraction, blur, background noise, and contamination
- Tissue coverage, ROI completeness, sampling adequacy, feature-level assessability, and scale metadata
- Loaded, unavailable, not-performed, and conflicting module outputs
- Evidence provenance, confidence, limitations, and expert-review requirement

Rules:
1. Apply Global_Rules.md.
2. Run quality assessment before fine-feature modules and final integration after all other loaded modules.
3. Integrate only fields explicitly produced by successfully loaded modules; do not fill missing results or add conclusions.
4. Do not convert `N/A`, `Not_Evaluable`, `Indeterminate`, `Not_Performed`, `Unavailable`, or `Unknown` to `Absent`.
5. Report conflicts without choosing an unsupported resolution.
6. Keep direct observations, uncertain findings, unavailable findings, non-evaluable findings, and limitations separate.
7. Do not extrapolate ROI findings to a whole slide or specimen.
8. If this module is unavailable, preserve individual module outputs and set `Integrated_Report_Status: Unavailable`.

Output:
- Image_Quality
- Overall_Assessability
- Module_Applicability_and_Loading_Status
- Structured_Morphological_Findings
- Morphological_Summary
- Uncertain_Findings
- Conflicting_Findings
- Unavailable_Findings
- Not_Evaluable_Findings
- Image_Limitations
- ROI_and_Sampling_Limitations
- Integrated_Report_Status
- Expert_Review_Required
- Expert_Review_Reason
