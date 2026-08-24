from __future__ import annotations

import asyncio
import io
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image
from starlette.datastructures import Headers

SERVER_DIR = Path(__file__).resolve().parents[1] / "server"
sys.path.insert(0, str(SERVER_DIR))
import pathovision_server
import student_vlm


class CaseArtifactTests(unittest.TestCase):
    def test_create_case_persists_roi_and_reloads_links(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            old_root = pathovision_server.CASE_ROOT
            pathovision_server.CASE_ROOT = Path(directory).resolve()
            try:
                image = Image.new("RGB", (40, 30), "white")
                regions = [{
                    "region_index": 1,
                    "detection_index": 1,
                    "label": "candidate",
                    "confidence": 0.8,
                    "bbox_xyxy": [5, 5, 20, 20],
                    "crop_bbox_xyxy": [4, 4, 21, 21],
                    "width": 17,
                    "height": 17,
                    "image": image.crop((4, 4, 21, 21)),
                }]
                metadata = pathovision_server.create_case(
                    image,
                    image.copy(),
                    [{
                        "index": 1,
                        "class_id": 0,
                        "class_name": "candidate",
                        "confidence": 0.8,
                        "bbox_xyxy": [5, 5, 20, 20],
                    }],
                    0.25,
                    0.45,
                    100,
                    {"status": "failed", "model_key": "mistral-small-3.1"},
                    regions,
                )
                case_dir = Path(directory) / metadata["case_id"]
                self.assertEqual(metadata["schema_version"], 3)
                self.assertEqual(metadata["model"]["key"], "yolo11m")
                self.assertEqual(metadata["model"]["weights"], "yolo11m_best.pt")
                model_keys = {item["key"] for item in pathovision_server.model_info()["localization_models"]}
                self.assertEqual(model_keys, {"yolo11m", "yolo11s"})
                self.assertTrue((case_dir / "roi_001.png").is_file())
                self.assertEqual(len(metadata["artifacts"]["regions"]), 1)
                loaded = pathovision_server.load_metadata(metadata["case_id"])
                self.assertEqual(loaded["files"]["regions"], ["roi_001.png"])
                self.assertTrue(loaded["artifacts"]["regions"][0].endswith("/regions/1"))
                self.assertNotIn("image", loaded["analysis"]["vlm_regions"][0])
            finally:
                pathovision_server.CASE_ROOT = old_root

    def test_gpu_fp16_uses_current_ultralytics_quantize_api(self) -> None:
        model = Mock()
        model.predict.return_value = [type("Result", (), {"boxes": None})()]
        with patch.object(pathovision_server, "get_model", return_value=model), patch.object(
            pathovision_server, "DEVICE", "0"
        ), patch.object(pathovision_server, "YOLO_HALF", True):
            localized, detections = pathovision_server.run_inference(
                Image.new("RGB", (32, 32), "white"), 0.25, 0.45, 100, "yolo11s"
            )
        kwargs = model.predict.call_args.kwargs
        self.assertEqual(kwargs["quantize"], 16)
        self.assertNotIn("half", kwargs)
        self.assertEqual(localized.size, (32, 32))
        self.assertEqual(detections, [])

    def test_zero_detections_never_invokes_student_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            old_root = pathovision_server.CASE_ROOT
            pathovision_server.CASE_ROOT = Path(directory).resolve()
            try:
                image = Image.new("RGB", (32, 32), "white")
                encoded = io.BytesIO()
                image.save(encoded, format="PNG")
                data = encoded.getvalue()
                upload = pathovision_server.UploadFile(
                    io.BytesIO(data),
                    size=len(data),
                    filename="no-detection.png",
                    headers=Headers({"content-type": "image/png"}),
                )
                fake_weight = Path(directory) / "yolo11m_best.pt"
                fake_weight.touch()
                with patch.dict(
                    pathovision_server.LOCALIZATION_MODEL_PATHS,
                    {"yolo11m": fake_weight},
                    clear=False,
                ), patch.object(
                    pathovision_server,
                    "run_inference",
                    return_value=(image.copy(), []),
                ), patch.object(
                    pathovision_server,
                    "analyze_with_student",
                    side_effect=AssertionError("analysis model must not run without a detection"),
                ) as student:
                    metadata = asyncio.run(
                        pathovision_server.create_analysis(
                            upload,
                            confidence=0.25,
                            iou=0.45,
                            max_detections=100,
                            localization_model="yolo11m",
                            student_model="",
                        )
                    )
                student.assert_not_called()
                self.assertEqual(metadata["detections"], [])
                self.assertEqual(metadata["analysis"]["student_vlm"]["status"], "not_requested")
                self.assertEqual(metadata["artifacts"]["regions"], [])
            finally:
                pathovision_server.CASE_ROOT = old_root

    def test_case_crud_create_rename_update_and_delete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            old_root = pathovision_server.CASE_ROOT
            pathovision_server.CASE_ROOT = Path(directory).resolve()
            try:
                created = pathovision_server.create_manual_case(
                    pathovision_server.CaseCreateRequest(case_id="CASE-001")
                )
                self.assertEqual(created["case_id"], "CASE-001")
                self.assertEqual(created["analysis"]["status"], "未分析")
                self.assertTrue((Path(directory) / "CASE-001" / "analysis.json").is_file())

                report = pathovision_server.ReportUpdate(
                    patient_id="PATIENT-9",
                    specimen_id="SPECIMEN-2",
                    report_status="待複核",
                )
                updated = pathovision_server.update_case_record(
                    "CASE-001",
                    pathovision_server.CaseUpdateRequest(
                        case_id="CASE-RENAMED",
                        report=report,
                    ),
                )
                self.assertEqual(updated["case_id"], "CASE-RENAMED")
                self.assertEqual(updated["report"]["patient_id"], "PATIENT-9")
                self.assertFalse((Path(directory) / "CASE-001").exists())
                self.assertTrue((Path(directory) / "CASE-RENAMED").is_dir())

                listed = pathovision_server.list_analyses()
                self.assertEqual([item.case_id for item in listed], ["CASE-RENAMED"])
                pathovision_server.delete_analysis("CASE-RENAMED")
                self.assertFalse((Path(directory) / "CASE-RENAMED").exists())
            finally:
                pathovision_server.CASE_ROOT = old_root

    def test_structured_report_edit_persists_and_locks_shape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            old_root = pathovision_server.CASE_ROOT
            pathovision_server.CASE_ROOT = Path(directory).resolve()
            try:
                image = Image.new("RGB", (32, 32), "white")
                structured = {
                    "Schema_Version": "2.0",
                    "Morphological_Summary": {
                        "Direct_Observations_Only": "original text",
                        "Uncertain_Findings": [],
                    },
                }
                metadata = pathovision_server.create_case(
                    image,
                    image.copy(),
                    [],
                    0.25,
                    0.45,
                    100,
                    {
                        "status": "completed",
                        "model_key": "gemma4",
                        "model_name": "Gemma4 31B",
                        "structured_output": structured,
                    },
                    [],
                )
                case_id = metadata["case_id"]
                edited = {
                    "Schema_Version": "2.0",
                    "Morphological_Summary": {
                        "Direct_Observations_Only": "使用者修訂內容",
                        "Uncertain_Findings": [],
                    },
                }
                updated = pathovision_server.update_structured_analysis(
                    case_id,
                    pathovision_server.StructuredAnalysisUpdate(
                        structured_output=edited
                    ),
                )
                vlm = updated["analysis"]["student_vlm"]
                self.assertTrue(vlm["user_edited"])
                self.assertEqual(
                    vlm["structured_output"]["Morphological_Summary"]["Direct_Observations_Only"],
                    "使用者修訂內容",
                )
                stored = pathovision_server.json.loads(
                    (Path(directory) / case_id / "student_vlm_analysis.json").read_text(
                        encoding="utf-8"
                    )
                )
                self.assertEqual(stored, edited)
                with self.assertRaises(pathovision_server.HTTPException) as caught:
                    pathovision_server.update_structured_analysis(
                        case_id,
                        pathovision_server.StructuredAnalysisUpdate(
                            structured_output={"Schema_Version": "2.0"}
                        ),
                    )
                self.assertEqual(caught.exception.status_code, 422)
            finally:
                pathovision_server.CASE_ROOT = old_root

    def test_selected_region_analysis_uses_only_requested_detection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            old_root = pathovision_server.CASE_ROOT
            pathovision_server.CASE_ROOT = Path(directory).resolve()
            try:
                image = Image.new("RGB", (100, 80), "white")
                metadata = pathovision_server.create_case(
                    image,
                    image.copy(),
                    [
                        {"index": 1, "class_id": 0, "class_name": "a", "confidence": 0.7, "bbox_xyxy": [0, 0, 30, 30]},
                        {"index": 2, "class_id": 0, "class_name": "b", "confidence": 0.9, "bbox_xyxy": [40, 20, 90, 70]},
                    ],
                    0.25,
                    0.45,
                    100,
                    None,
                    [],
                    "yolo11s",
                )
                spec = student_vlm.SPEC_BY_KEY["gemma4"]
                completed = {
                    "status": "completed",
                    "model_key": spec.key,
                    "model_name": spec.display_name,
                    "model_id": spec.model_id,
                    "summary": "selected region visible",
                    "structured_output": {"ok": True},
                    "input_mode": "selected_yolo_rois",
                }
                with patch.object(
                    pathovision_server,
                    "require_student_model",
                    return_value=(spec, {}),
                ), patch.object(
                    pathovision_server,
                    "analyze_with_student",
                    return_value=completed,
                ) as analyze:
                    updated = pathovision_server.analyze_selected_case_regions(
                        metadata["case_id"],
                        pathovision_server.StudentRegionAnalysisRequest(
                            student_model="gemma4", detection_indices=[2]
                        ),
                    )
                supplied = analyze.call_args.kwargs
                self.assertEqual([item["index"] for item in supplied["detections"]], [2])
                self.assertEqual(updated["parameters"]["selected_detection_indices"], [2])
                self.assertEqual(updated["parameters"]["localization_model"], "yolo11s")
                self.assertEqual(updated["analysis"]["vlm_regions"][0]["detection_index"], 2)
                self.assertEqual(len(updated["artifacts"]["regions"]), 1)
                self.assertTrue((Path(directory) / metadata["case_id"] / "roi_001.png").is_file())
            finally:
                pathovision_server.CASE_ROOT = old_root


    def test_multiple_selected_regions_run_in_parallel_and_persist_separately(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            old_root = pathovision_server.CASE_ROOT
            pathovision_server.CASE_ROOT = Path(directory).resolve()
            try:
                image = Image.new("RGB", (120, 90), "white")
                detections = [
                    {"index": 1, "class_id": 0, "class_name": "a", "confidence": 0.9, "bbox_xyxy": [0, 0, 40, 40]},
                    {"index": 2, "class_id": 0, "class_name": "b", "confidence": 0.8, "bbox_xyxy": [55, 30, 110, 85]},
                ]
                metadata = pathovision_server.create_case(
                    image, image.copy(), detections, 0.25, 0.45, 100,
                    None, [], "yolo11m",
                )
                spec = student_vlm.SPEC_BY_KEY["gemma4"]
                barrier = threading.Barrier(2)

                def analyze_one(**kwargs):
                    barrier.wait(timeout=2)
                    detection_index = kwargs["detections"][0]["index"]
                    return {
                        "status": "completed",
                        "model_key": spec.key,
                        "model_name": spec.display_name,
                        "model_id": spec.model_id,
                        "summary": f"region {detection_index}",
                        "structured_output": {"region": detection_index},
                        "controls": {"prompt": "best_prompt/Prompt.md"},
                    }

                with patch.object(
                    pathovision_server,
                    "require_student_model",
                    return_value=(spec, {}),
                ), patch.object(
                    pathovision_server,
                    "analyze_with_student",
                    side_effect=analyze_one,
                ) as analyze:
                    updated = pathovision_server.analyze_selected_case_regions(
                        metadata["case_id"],
                        pathovision_server.StudentRegionAnalysisRequest(
                            student_model="gemma4", detection_indices=[1, 2]
                        ),
                    )
                reports = updated["analysis"]["student_vlm"]["region_reports"]
                self.assertEqual(analyze.call_count, 2)
                self.assertEqual({item["detection_index"] for item in reports}, {1, 2})
                self.assertTrue(all(item["status"] == "completed" for item in reports))
                self.assertEqual(updated["analysis"]["student_vlm"]["status"], "completed")
                self.assertEqual(len(updated["files"]["student_vlm_regions"]), 2)
                for filename in updated["files"]["student_vlm_regions"]:
                    self.assertTrue((Path(directory) / metadata["case_id"] / filename).is_file())

                edited = pathovision_server.update_structured_analysis(
                    metadata["case_id"],
                    pathovision_server.StructuredAnalysisUpdate(
                        structured_output={"region": 20}, detection_index=2
                    ),
                )
                edited_reports = edited["analysis"]["student_vlm"]["region_reports"]
                outputs = {
                    item["detection_index"]: item["structured_output"]["region"]
                    for item in edited_reports
                }
                self.assertEqual(outputs, {1: 1, 2: 20})
                edited_second = next(
                    item for item in edited_reports if item["detection_index"] == 2
                )
                self.assertTrue(edited_second["user_edited"])
            finally:
                pathovision_server.CASE_ROOT = old_root


    def test_two_models_two_regions_are_preserved_as_four_reports(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            old_root = pathovision_server.CASE_ROOT
            pathovision_server.CASE_ROOT = Path(directory).resolve()
            try:
                image = Image.new("RGB", (120, 90), "white")
                detections = [
                    {
                        "index": 1,
                        "class_id": 0,
                        "class_name": "a",
                        "confidence": 0.9,
                        "bbox_xyxy": [0, 0, 40, 40],
                    },
                    {
                        "index": 2,
                        "class_id": 0,
                        "class_name": "b",
                        "confidence": 0.8,
                        "bbox_xyxy": [55, 30, 110, 85],
                    },
                ]
                metadata = pathovision_server.create_case(
                    image,
                    image.copy(),
                    detections,
                    0.25,
                    0.45,
                    100,
                    None,
                    [],
                    "yolo11m",
                )
                case_id = metadata["case_id"]

                def run_model(model_key: str, marker_text: str, indices: list[int]):
                    spec = student_vlm.SPEC_BY_KEY[model_key]

                    def analyze_one(**kwargs):
                        detection_index = kwargs["detections"][0]["index"]
                        return {
                            "status": "completed",
                            "model_key": spec.key,
                            "model_name": spec.display_name,
                            "model_id": spec.model_id,
                            "summary": f"{marker_text} region {detection_index}",
                            "structured_output": {
                                "model": marker_text,
                                "region": detection_index,
                            },
                        }

                    with patch.object(
                        pathovision_server,
                        "require_student_model",
                        return_value=(spec, {}),
                    ), patch.object(
                        pathovision_server,
                        "analyze_with_student",
                        side_effect=analyze_one,
                    ):
                        return pathovision_server.analyze_selected_case_regions(
                            case_id,
                            pathovision_server.StudentRegionAnalysisRequest(
                                student_model=model_key,
                                detection_indices=indices,
                            ),
                        )

                first = run_model("gemma4", "gemma", [1, 2])
                self.assertEqual(
                    len(first["analysis"]["student_vlm"]["region_reports"]), 2
                )

                second = run_model("mistral-small-3.1", "mistral", [1, 2])
                reports = second["analysis"]["student_vlm"]["region_reports"]
                self.assertEqual(len(reports), 4)
                self.assertEqual(
                    {
                        (report["model_key"], report["detection_index"])
                        for report in reports
                    },
                    {
                        ("gemma4", 1),
                        ("gemma4", 2),
                        ("mistral-small-3.1", 1),
                        ("mistral-small-3.1", 2),
                    },
                )
                self.assertEqual(len(second["files"]["student_vlm_regions"]), 4)
                self.assertEqual(
                    set(second["files"]["student_vlm_regions"]),
                    {
                        "student_vlm_gemma4_region_001.json",
                        "student_vlm_gemma4_region_002.json",
                        "student_vlm_mistral-small-3.1_region_001.json",
                        "student_vlm_mistral-small-3.1_region_002.json",
                    },
                )

                rerun = run_model("gemma4", "gemma-rerun", [1])
                rerun_reports = rerun["analysis"]["student_vlm"]["region_reports"]
                self.assertEqual(len(rerun_reports), 4)
                outputs = {
                    (report["model_key"], report["detection_index"]): report[
                        "structured_output"
                    ]["model"]
                    for report in rerun_reports
                }
                self.assertEqual(outputs[("gemma4", 1)], "gemma-rerun")
                self.assertEqual(outputs[("gemma4", 2)], "gemma")
                self.assertEqual(outputs[("mistral-small-3.1", 1)], "mistral")
                self.assertEqual(outputs[("mistral-small-3.1", 2)], "mistral")
            finally:
                pathovision_server.CASE_ROOT = old_root


if __name__ == "__main__":
    unittest.main()
