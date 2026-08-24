# Control-Layer Revision Map

The 20 files in `Skills/` are preserved without content changes. Only the surrounding control and evaluation files were revised for structured pathology image assistance and SkillOpt integration.

| File | Change |
|---|---|
| `Prompt.md` | Defines non-diagnostic pathology image analysis, separates final morphology output from SkillOpt evaluation data, and limits SkillOpt to one selected skill per run. |
| `Global_Rules.md` | Standardizes finding statuses and uses `Supporting_Visible_Evidence` consistently. |
| `Output_Schema.json` | Uses analysis-oriented terminology, adds integrated analysis status, and aligns all findings with the same value/status/evidence structure. |
| `Output_Field_Skill_Mapping.yaml` | Defines the authoritative final-field-to-Skill mapping, native conditional outputs, and SkillOpt scoring scope for every Skill. |
| `Skill_Registry.yaml` | Moves tissue-border analysis to conditional loading and defines one-skill-at-a-time optimization. |
| `SkillOpt_Evaluation_Schema.json` | Defines the external Teacher-versus-Student evaluation record. |
| `SkillOpt_Config.yaml` | Uses the official SkillOpt structured YAML sections and configures the `pathology_morphology` environment, frozen target rollout, validation gate, and scoring weights. |
| `Tools/validate_project.py` | Deterministically checks JSON/YAML syntax, Skill structure, registry coverage, mapping validity, scoring weights, and unchanged Skill bytes. |

The filename `Pathology_Image_Quality_and_Structured_Morphology_Reporting.md` is retained because the user requested that skill files remain unchanged. Within the revised control layer, it functions as the image-quality and final integration module; its legacy internal terminology is mapped to `Analysis_Metadata.Integrated_Analysis_Status` and the output names defined by `Output_Schema.json`.
