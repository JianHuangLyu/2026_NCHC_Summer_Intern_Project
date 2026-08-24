Skill_Name: Tissue_Component_Recognition

Description:
This revised skill identifies general tissue and material components directly visible in a pathology ROI without assuming an organ or lesion type.

Task:
Identify major and minor visible components, their approximate proportions, distribution, and spatial relationships.

Assess:
- Surface, glandular, ductal, squamous, urothelial-like, or other epithelium
- Mesenchymal, fibrous, adipose, smooth-muscle, skeletal-muscle, peripheral-nerve, neural/glial, lymphoid, or hematopoietic tissue
- Bone, cartilage, vascular structures, serosal or mesothelial lining, and organ-specific parenchyma
- Extracellular matrix, inflammatory-cell-rich areas, hemorrhage, necrotic or acellular material, foreign material, and background
- Predominant component, additional components, proportion, distribution, adjacency, and unclassified tissue

Rules:
1. Apply Global_Rules.md.
2. Identify a component only when visible morphology is sufficient; otherwise use `Indeterminate` or `Unclassified_Tissue_Component`.
3. Do not convert a visible component into a disease, lineage, differentiation, or tumour classification.
4. Do not force unfamiliar tissue into epithelium, stroma, or background.
5. Report approximate proportions as visual estimates and do not imply whole-slide proportions from a small ROI.

Output:
- Predominant_Tissue_Component
- Additional_Tissue_Components
- Component_Proportions
- Component_Distribution
- Component_Adjacency
- Spatial_Organization
- Unclassified_Tissue_Component
- Tissue_Coverage
- Assessability
- Limitations
