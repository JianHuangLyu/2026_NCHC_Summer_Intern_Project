from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_DIR))
import student_vlm


class StudentVLMRoiTests(unittest.TestCase):
    def test_roi_sort_clamp_limit_and_serialization(self) -> None:
        image = Image.new("RGB", (100, 80), "white")
        detections = [
            {"index": 1, "class_name": "low", "confidence": 0.2, "bbox_xyxy": [20, 20, 40, 40]},
            {"index": 2, "class_name": "high", "confidence": 0.9, "bbox_xyxy": [-10, 10, 60, 90]},
            {"index": 3, "class_name": "invalid", "confidence": 1.0, "bbox_xyxy": [2, 2, 2, 4]},
        ]
        regions = student_vlm.prepare_roi_regions(
            image, detections, max_regions=1, padding_ratio=0
        )
        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0]["detection_index"], 2)
        self.assertEqual(regions[0]["crop_bbox_xyxy"], [0, 10, 60, 80])
        self.assertEqual(regions[0]["image"].size, (60, 70))
        serialized = student_vlm.serialize_roi_regions(regions)
        self.assertNotIn("image", serialized[0])
        self.assertEqual(
            student_vlm.prepare_roi_regions(image, detections, max_regions=0), []
        )

    def test_payload_contains_only_selected_roi_images(self) -> None:
        image = Image.new("RGB", (64, 64), "white")
        detections = [
            {"index": 1, "class_name": "a", "confidence": 0.9, "bbox_xyxy": [0, 0, 32, 32]},
            {"index": 2, "class_name": "b", "confidence": 0.8, "bbox_xyxy": [32, 32, 64, 64]},
        ]
        regions = student_vlm.prepare_roi_regions(
            image, detections, max_regions=2, padding_ratio=0
        )
        captured = {}

        def fake_completion(**kwargs):
            captured.update(kwargs["payload"])
            return {
                "choices": [{"message": {"content": '{"Morphological_Summary":{"Direct_Observations_Only":"visible"}}'}}],
                "usage": {"total_tokens": 1},
            }

        spec = student_vlm.SPEC_BY_KEY["mistral-small-3.1"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prompt = root / spec.folder / "best_prompt"
            prompt.mkdir(parents=True)
            (prompt / "Skill_Registry.yaml").write_text("required: []\n", encoding="utf-8")
            with patch.object(student_vlm, "require_student_model", return_value=(spec, {})), patch.object(
                student_vlm,
                "compose_system_prompt",
                return_value=("controls", {"type": "object"}),
            ), patch.object(student_vlm, "_http_completion", side_effect=fake_completion), patch.dict(
                os.environ,
                {
                    spec.endpoint_env: "http://127.0.0.1:9999/v1",
                },
                clear=False,
            ):
                result = student_vlm.analyze_with_student(
                    root=root,
                    model_key=spec.key,
                    image=image,
                    detections=detections,
                    roi_regions=regions,
                )
        content = captured["messages"][1]["content"]
        image_parts = [item for item in content if item.get("type") == "image_url"]
        self.assertEqual(len(image_parts), 2)
        self.assertEqual(result["input_image_count"], 2)
        self.assertEqual(result["input_mode"], "selected_yolo_rois")
        self.assertEqual(len(result["regions"]), 2)
        self.assertEqual(result["summary"], "visible")
        self.assertEqual(content[0]["type"], "image_url")
        self.assertEqual(content[-1]["type"], "text")
        self.assertIn("input_image_manifest", content[-1]["text"])
        self.assertIn("professional Traditional Chinese", content[-1]["text"])
        self.assertIn("zh-Hant-TW", content[-1]["text"])
        self.assertEqual(result["controls"]["narrative_language"], "zh-Hant-TW")
        self.assertEqual(captured["max_tokens"], 8192)

    def test_prompt_compaction_preserves_best_bundle_schema_and_skills(self) -> None:
        spec = student_vlm.SPEC_BY_KEY["mistral-small-3.1"]
        schema = {
            "type": "object",
            "properties": {"Value": {"type": "string"}},
            "required": ["Value"],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prompt_dir = root / spec.folder / "best_prompt"
            skill_dir = root / spec.folder / "best_skills"
            prompt_dir.mkdir(parents=True)
            skill_dir.mkdir(parents=True)
            (prompt_dir / "Prompt.md").write_text("BEST PROMPT", encoding="utf-8")
            (prompt_dir / "Global_Rules.md").write_text("GLOBAL RULES", encoding="utf-8")
            (prompt_dir / "Skill_Registry.yaml").write_text(
                "required:\n  - Morphology.md\n", encoding="utf-8"
            )
            (prompt_dir / "Output_Schema.json").write_text(
                __import__("json").dumps(schema, indent=2), encoding="utf-8"
            )
            (prompt_dir / "Output_Field_Skill_Mapping.yaml").write_text(
                "Value: Morphology", encoding="utf-8"
            )
            (skill_dir / "Morphology.md").write_text("BEST SKILL", encoding="utf-8")
            student_vlm.compose_system_prompt.cache_clear()
            system_prompt, loaded_schema = student_vlm.compose_system_prompt(root, spec)

        self.assertEqual(loaded_schema, schema)
        self.assertIn("BEST PROMPT", system_prompt)
        self.assertIn("GLOBAL RULES", system_prompt)
        self.assertIn("BEST SKILL", system_prompt)
        self.assertIn('{"type":"object","properties":{"Value":{"type":"string"}}', system_prompt)
        self.assertNotIn('{\n  "type": "object"', system_prompt)

    def test_guided_schema_removes_unique_items_without_mutating_source(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "values": {
                    "type": "array",
                    "uniqueItems": True,
                    "items": {"type": "string"},
                }
            },
        }
        guided = student_vlm._guided_decoding_schema(schema)
        self.assertNotIn("uniqueItems", guided["properties"]["values"])
        self.assertTrue(schema["properties"]["values"]["uniqueItems"])

    def test_student_analysis_requires_at_least_one_selected_roi(self) -> None:
        image = Image.new("RGB", (64, 64), "white")
        spec = student_vlm.SPEC_BY_KEY["gemma4"]
        with patch.object(
            student_vlm, "require_student_model", return_value=(spec, {})
        ), patch.object(
            student_vlm, "compose_system_prompt", return_value=("controls", {"type": "object"})
        ), patch.dict(
            os.environ,
            {spec.endpoint_env: "http://127.0.0.1:9999/v1"},
            clear=False,
        ):
            with self.assertRaisesRegex(student_vlm.StudentVLMError, "No selected YOLO regions"):
                student_vlm.analyze_with_student(
                    root=Path("."),
                    model_key=spec.key,
                    image=image,
                    detections=[],
                    roi_regions=[],
                )


if __name__ == "__main__":
    unittest.main()
