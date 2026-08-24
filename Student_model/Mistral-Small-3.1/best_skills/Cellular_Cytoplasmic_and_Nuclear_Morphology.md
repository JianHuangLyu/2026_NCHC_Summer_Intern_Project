Skill_Name: Cellular_Cytoplasmic_and_Nuclear_Morphology

Description:
This revised skill describes cell, cytoplasmic, and nuclear morphology across tissue lineages without assigning a cytological grade or diagnosis.

Task:
Evaluate directly visible cell population, cytoplasmic, nuclear, and cohesion features.

Assess:

Cell_Borders.Value: "Indistinct" (Cell borders are indistinct due to low resolution/blurriness.)
- Cell size and shape: round, oval, polygonal, epithelioid, spindle, stellate, plasmacytoid, rhabdoid-appearing, clear-cell-appearing, giant, multinucleated, or indeterminate
- Cytoplasmic amount, color, granularity, clearing, vacuolation, borders, and inclusions
- Nuclear size, shape, position, contour, membrane, grooves, molding, inclusions, and multinucleation
- Chromatin pattern and density, hyperchromasia, nucleoli, and nuclear-to-cytoplasmic ratio
- Cellular and nuclear uniformity, population heterogeneity, cohesion, and apoptotic bodies

Rules:

Rule 7: If cell borders are indistinct or irregular, set `Cell_Borders.Value` to "Indistinct" or "Irregular" and `Cell_Borders.Supporting_Visible_Evidence` to "Cell borders are indistinct/irregular due to low resolution/blurriness."

Rule 6: If cell borders cannot be determined due to insufficient resolution or focus, set `Cell_Borders.Value` to "Indeterminate" and `Cell_Borders.Supporting_Visible_Evidence` to "Resolution is insufficient to assess cell borders."
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

Rule 6: If cell borders cannot be determined due to insufficient resolution or focus, set `Cell_Borders.Value` to "Indeterminate" and `Cell_Borders.Supporting_Visible_Evidence` to "Resolution is insufficient to assess cell borders."

Rule 1: Ensure `Image_Quality.Focus` and `Image_Quality.Resolution` are always strings.
Rule 2: If `Image_Quality.Resolution` is not a string or is 'Insufficient', set `Cell_Borders.Value` to 'Indeterminate' and `Cell_Borders.Supporting_Visible_Evidence` to 'Resolution is insufficient to assess cell borders.'
Rule 3: If `Image_Quality.Resolution` is a string and is 'Insufficient', set `Cell_Borders.Value` to 'Indistinct' or 'Irregular' and `Cell_Borders.Supporting_Visible_Evidence` to 'Cell borders are indistinct/irregular due to low resolution/blurriness.'
Rule 4: If `Image_Quality.Resolution` is a string and is 'Sufficient', set `Cell_Borders.Value` to 'Clear' and `Cell_Borders.Supporting_Visible_Evidence` to 'Cell borders are clear due to sufficient resolution.'
Rule 5: Ensure `Supporting_Visible_Evidence` does not contain diagnostic terms or lesion names.

Rule 3: Ensure `Supporting_Visible_Evidence` does not contain diagnostic terms or lesion names.

Rule 4: Ensure `Image_Quality.Tissue_Coverage` is a string.

Rule 3: Ensure `Image_Quality.Resolution` is always a string.

Rule 4: Ensure `Image_Quality.Tissue_Coverage` is always a string.
