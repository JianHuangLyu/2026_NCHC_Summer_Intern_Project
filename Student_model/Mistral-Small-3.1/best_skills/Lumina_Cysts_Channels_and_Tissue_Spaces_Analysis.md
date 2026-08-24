Skill_Name: Lumina_Cysts_Channels_and_Tissue_Spaces_Analysis

Description:
This revised conditional skill evaluates epithelial lumina, glandular or ductal spaces, cystic spaces, vascular-like channels, intracellular lumina or vacuoles, pseudocystic spaces, tissue clefts, and processing spaces.

Task:
Classify and describe visible spaces and their contents while preserving uncertainty between true structures and artifact.

Assess:
- Space type, lining, number, size, shape, distribution, dilation, and distortion
- Round, oval, slit-like, compressed, irregular, angulated, cystic, intracellular, or indeterminate spaces
- Empty, proteinaceous, eosinophilic, mucin-like, inflammatory, cellular, necrotic-appearing, hemorrhagic, calcified, or nonspecific content
- Tissue separation, retraction, tearing, and processing artifact

Rules:
1. Apply Global_Rules.md.
2. Use `N/A` when no relevant space is present.
3. Do not classify a space as epithelial or vascular unless the lining is sufficiently visible.
4. If content cannot be identified, use `nonspecific material` or `Indeterminate`.
5. Do not infer a disease or lesion from space morphology or content.

Output:
- Space_Presence
- Space_Type
- Lining_Morphology
- Number
- Size
- Shape
- Distribution
- Distortion_or_Dilation
- Content
- Possible_Artifact
- Assessability
- Limitations
