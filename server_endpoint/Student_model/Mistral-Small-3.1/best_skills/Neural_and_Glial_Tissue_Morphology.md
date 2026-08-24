Skill_Name: Neural_and_Glial_Tissue_Morphology

Description:
This new conditional skill describes visible neural, glial, neuropil-like, and peripheral-nerve structures without assigning cell lineage or CNS/PNS diagnosis.

Task:
Evaluate neural or glial tissue components, cellular arrangement, background, and vascular relationships.

Assess:
- Neuropil-like background, fibrillary matrix, nerve fascicles, axon-like fibers, ganglion-like cells, and myelin-like clearing
- Diffuse, fascicular, palisaded, rosette-like, perivascular pseudorosette-like, whorled, or biphasic arrangement
- Glial-like, neuronal-like, spindle, epithelioid, round, or mixed cells
- Microcystic change, calcification, vascular proliferation-like change, necrosis, and satellitosis-like arrangement when visible

Rules:
1. Apply Global_Rules.md.
2. Use `-like` or `-appearing` because H&E morphology alone may not confirm neural or glial lineage.
3. Do not infer tumour type, grade, molecular class, or CNS/PNS origin from a single feature.
4. Do not label vascular proliferation or satellitosis unless morphology is sufficiently resolved.
5. Mark features `Not_Evaluable` when resolution is inadequate.

Output:
- Neural_or_Glial_Component
- Background_or_Matrix
- Cellular_Morphology
- Architectural_Pattern
- Nerve_Fascicle_or_Fiber_Organization
- Rosette_Palisading_or_Perivascular_Arrangement
- Microcystic_or_Vascular_Features
- Assessability
- Limitations
