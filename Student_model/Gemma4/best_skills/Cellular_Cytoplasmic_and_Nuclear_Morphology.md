Skill_Name: Cellular_Cytoplasmic_and_Nuclear_Morphology

Description:
This revised skill describes cell, cytoplasmic, and nuclear morphology across tissue lineages without assigning a cytological grade or diagnosis.

Task:
Evaluate directly visible cell population, cytoplasmic, nuclear, and cohesion features.

Assess:
- Cell size and shape: round, oval, polygonal, epithelioid, spindle, stellate, plasmacytoid, rhabdoid-appearing, clear-cell-appearing, giant, multinucleated, or indeterminate
- Cytoplasmic amount, color, granularity, clearing, vacuolation, borders, and inclusions
- Nuclear size, shape, position, contour, membrane, grooves, molding, inclusions, and multinucleation
- Chromatin pattern and density, hyperchromasia, nucleoli, and nuclear-to-cytoplasmic ratio
- Cellular and nuclear uniformity, population heterogeneity, cohesion, and apoptotic bodies

Rules:
1. Apply Global_Rules.md.
2. Do not label cells as malignant, cancerous, high-grade, or lineage-confirmed.
3. Use descriptive `-like` or `-appearing` terms when composition or lineage cannot be confirmed visually.
4. Set chromatin, nucleoli, nuclear contour, and small inclusions to `Not_Evaluable` when magnification or resolution is inadequate.
5. Distinguish true cytoplasmic clearing or vacuoles from processing artifact when possible.

Output:
- Cell_Size
- Predominant_Cell_Shape
- Secondary_Cell_Shapes
- Cytoplasmic_Amount
- Cytoplasmic_Quality
- Cell_Borders
- Cytoplasmic_Vacuoles_or_Inclusions
- Nuclear_Size
- Nuclear_Shape
- Nuclear_Position
- Nuclear_Size_and_Shape_Variation
- Nuclear_Contour_and_Membrane
- Nuclear_Grooves_Molding_or_Inclusions
- Chromatin
- Nuclear_Hyperchromasia
- Prominent_Nucleoli
- Nuclear_to_Cytoplasmic_Ratio
- Multinucleation
- Cellular_Cohesion
- Population_Uniformity_and_Heterogeneity
- Apoptotic_Bodies
- Assessability
- Limitations

Add assessment categories: Indistinct, Irregular
Add rule: When Cell_Borders are indistinct, the assessment is 'Indistinct'.
Add rule: When Cell_Borders are irregular, the assessment is 'Irregular'.

Add rule: When Cell_Borders are Clear, the assessment is 'Clear'.
Add rule: When Cell_Borders are Well-defined, the assessment is 'Well-defined'.

Add assessment categories: Indistinct, Irregular

Add rule: When Cell_Borders are Clear, the assessment is 'Clear'.

Add rule: Do not include diagnoses, lesion names, molecular status, immunohistochemical status, in-situ or invasive classification, or other non-morphological assessments in the output.

Add rule: When the `Supporting_Visible_Evidence` field is empty or does not clearly support the predicted `Value`, the `Value` should be set to 'Indistinct' or 'Irregular' based on the context (e.g., crowding, resolution, blur).
