from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

CLIENT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(CLIENT_DIR))
fake_gradio = types.ModuleType("gradio")

class FakeProgress:
    def __init__(self, *args, **kwargs):
        pass

    def __call__(self, *args, **kwargs):
        return None

fake_gradio.Progress = FakeProgress
fake_gradio.SelectData = object
sys.modules.setdefault("gradio", fake_gradio)
import app


class OutputSchemaUiTests(unittest.TestCase):
    def test_schema_sections_and_report_mapping(self) -> None:
        output = {
            "Schema_Version": "2.0",
            "Analysis_Metadata": {"Integrated_Analysis_Status": "Available", "Loaded_Skills": ["Morphology"]},
            "Image_Context": {"Image_Type": "ROI", "Stain": "H&E"},
            "Image_Quality": {"Overall_Assessability": "Adequate"},
            "Findings": {
                "Tissue_Components": {
                    "Predominant_Component": {
                        "Value": "epithelial cells",
                        "Status": "Present",
                        "Supporting_Visible_Evidence": ["cohesive groups"],
                    }
                },
                "Cellular_Cytoplasmic_and_Nuclear_Morphology": {},
                "General_Tissue_Architecture": {},
                "Extracellular_Matrix_and_Stroma": {},
                "Special_Findings": {},
                "Conditional_Findings": {
                    "Activated_Modules": ["Tissue_Border_Interface_and_Growth_Pattern"],
                    "Module_Findings": {
                        "Tissue_Border_Interface_and_Growth_Pattern": {
                            "Border_Morphology": {
                                "Value": "Pushing-appearing",
                                "Status": "Present",
                                "Supporting_Visible_Evidence": [
                                    "A clear interface exists between the cellular mass and the fibrous stroma on the right"
                                ],
                            },
                            "Supporting_Visible_Features": {
                                "Value": ["Nuclear pleomorphism", "Solid growth pattern"],
                                "Status": "Present",
                                "Supporting_Visible_Evidence": [
                                    "Complete loss of any organized tissue pattern"
                                ],
                            },
                        }
                    },
                },
            },
            "Morphological_Summary": {
                "Direct_Observations_Only": "Visible cohesive cells.",
                "Uncertain_Findings": ["small focus"],
                "Not_Evaluable_Findings": [],
            },
            "Limitations": {
                "Image_Limitations": ["single field"],
                "ROI_Limitations": [],
                "Sampling_Limitations": [],
                "Human_Review_Suggested": True,
                "Human_Review_Reason": "Research-only output",
            },
        }
        rendered = app.build_schema_output(output)
        for expected in ("分析資訊與技能狀態", "影像品質與可判讀性", "組織成分與分布", "細胞質與細胞核形態", "特殊病理形態所見", "分析限制與人工複核", "epithelial cells"):
            self.assertIn(expected, rendered)
        report = app.schema_report_text(output)
        self.assertIn("直接形態觀察", report)
        self.assertIn("分析限制", report)
        self.assertIn("人工複核", report)

        visual = app.build_structured_report(output, "CASE-001", "Gemma 4 31B")
        for expected in (
            "病理形態結構化報告", "CASE-001", "Gemma 4 31B", "整合形態學摘要",
            "影像與檢體脈絡／可判讀性", "Image and Specimen Context / Assessability", "分析限制與人工複核",
            "cohesive groups", "組織邊界、介面與生長型態分析", "組織邊界形態",
            "呈推擠性邊界外觀", "細胞性團塊與右側纖維性間質之間可見清楚介面",
            "支持性可見形態特徵", "細胞核多形性", "實性生長型態",
            "完全喪失可辨識的規則組織排列",
        ):
            self.assertIn(expected, visual)
        for untranslated in (
            "Tissue_Border_Interface_and_Growth_Pattern", "Border_Morphology",
            "Pushing-appearing", "Nuclear pleomorphism", "Solid growth pattern",
            "Complete loss of any organized tissue pattern", "Supporting_Visible_Features",
        ):
            self.assertNotIn(untranslated, visual)
        self.assertIn("Border Morphology", visual)
        self.assertIn("Supporting Visible Evidence", visual)
        self.assertNotIn("病理分析技能使用狀態", visual)
        self.assertEqual(app._schema_text("Variable"), "具變異性")
        revised = app.build_structured_report(
            output, "CASE-001", "Gemma 4 31B", user_edited=True
        )
        self.assertIn("使用者修訂版", revised)
        escaped = app.build_structured_report({
            "Morphological_Summary": {
                "Direct_Observations_Only": "<script>alert(1)</script>"
            }
        })
        self.assertNotIn("<script>", escaped)
        self.assertIn("&lt;script&gt;", escaped)


if __name__ == "__main__":
    unittest.main()
