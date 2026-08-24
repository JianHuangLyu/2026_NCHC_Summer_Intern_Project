from __future__ import annotations

import inspect
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

fake_gradio = types.ModuleType("gradio")

class FakeProgress:
    def __init__(self, *args, **kwargs):
        pass

    def __call__(self, *args, **kwargs):
        return None

class FakeError(Exception):
    pass

fake_gradio.Progress = FakeProgress
fake_gradio.Error = FakeError
fake_gradio.SelectData = object
fake_gradio.update = lambda **kwargs: kwargs
sys.modules.setdefault("gradio", fake_gradio)

import app
from PIL import Image


class _FakeAPI:
    def __init__(self, *_args, **_kwargs) -> None:
        pass

    def health(self) -> dict[str, str]:
        return {"status": "ok"}

    def model(self) -> dict[str, object]:
        return {
            "key": "yolo11m",
            "weights": "test.pt",
            "default_student_model": "student",
            "localization_models": [
                {"key": "yolo11m", "display_name": "YOLO11m", "weights": "test.pt", "ready": True}
            ],
            "student_models": self.student_models(),
        }

    def student_models(self) -> list[dict[str, object]]:
        return [
            {
                "key": "student",
                "display_name": "Student",
                "parameter_scale": "test",
                "inference_ready": True,
            }
        ]


class SlurmStartupStreamingTests(unittest.TestCase):
    def test_no_ready_vlm_leaves_structured_model_unselected(self) -> None:
        api = Mock()
        update = app.student_model_update(
            api,
            {"default_student_model": "gemma4", "student_models": []},
        )
        self.assertIsNone(update["value"])
        self.assertEqual(update["choices"], [])
        self.assertFalse(update["interactive"])
        api.student_models.assert_not_called()

    def test_requested_model_display_names_and_selected_roi_preview(self) -> None:
        localization = app.localization_model_update({
            "key": "yolo11m",
            "localization_models": [
                {"key": "yolo11s", "ready": True},
                {"key": "yolo11m", "ready": True},
            ],
        })
        self.assertEqual(
            [label for label, _value in localization["choices"]],
            [
                "YOLO11s　推論快且較準確",
                "YOLO11m　推論稍慢且最準確",
            ],
        )
        api = Mock()
        students = app.student_model_update(api, {
            "student_models": [
                {"key": "mistral-small-3.1", "inference_ready": True},
                {"key": "gemma4", "inference_ready": True},
            ]
        })
        self.assertEqual(
            [label for label, _value in students["choices"]],
            [
                "Mistral Small 3.1 24B　推論較快但理解及推理次佳",
                "Gemma4 31B　推論較慢但理解及推理最佳",
            ],
        )
        source = Image.new("RGB", (100, 80), "white")
        preview = app.draw_selected_regions(
            source,
            [{"index": 2, "bbox_xyxy": [20, 15, 60, 50]}],
            ["2"],
        )
        self.assertEqual(source.getpixel((20, 15)), (255, 255, 255))
        self.assertEqual(preview.getpixel((20, 15)), (239, 35, 60))

    def test_roi_selection_preview_is_local_and_immediate(self) -> None:
        source = Image.new("RGB", (100, 80), "white")
        detections = [{"index": 2, "bbox_xyxy": [20, 15, 60, 50]}]
        with patch.object(app, "api_for", side_effect=AssertionError("REST must not run")):
            button, preview = app.selected_region_preview(
                source, detections, "CASE-1", "gemma4", ["2"]
            )
        self.assertTrue(button["interactive"])
        self.assertEqual(preview.getpixel((20, 15)), (239, 35, 60))

    def test_structured_report_editor_and_periodic_rewrite_are_removed(self) -> None:
        source = inspect.getsource(app.create_ui)
        self.assertNotIn("編輯此區域報告", source)
        self.assertNotIn("report_editor", source)
        self.assertNotIn("autosave_structured_report_edits", source)
        self.assertIn('elem_id="structured-model-selector"', source)
        self.assertIn('elem_id="structured-region-selector"', source)
        self.assertIn("選擇要查看報告的分析推論模型", source)
        self.assertIn("選擇該模型已完成分析的異常區域", source)
        self.assertNotIn("切換分析推論模型／異常區域報告", source)
    def test_login_page_is_nano4_only_and_uses_fixed_connection_values(self) -> None:
        source = inspect.getsource(app.create_ui)
        self.assertIn("NCHC NANO4 使用者登入", source)
        self.assertIn('label="NANO4 公開登入主機", interactive=False', source)
        self.assertIn('label="SSH Port", interactive=False', source)
        self.assertIn('local_port = gr.State(8765)', source)
        for removed in (
            "自動 NANO4 · SSH + Slurm", "連接既有 REST Endpoint",
            "Local SOCKS Port", "重新檢查並建立 Tunnel",
            "Client 使用 Windows 原生 ssh.exe＋ConPTY",
        ):
            self.assertNotIn(removed, source)


    def test_localization_stage_forces_yolo_only_and_passes_selected_yolo(self) -> None:
        class LocalizationAPI:
            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []

            def analyze(
                self,
                _image,
                _confidence,
                _iou,
                _max_detections,
                student_model=None,
                localization_model=None,
            ):
                self.calls.append(
                    {
                        "student_model": student_model,
                        "localization_model": localization_model,
                    }
                )
                return {
                    "case_id": "PV-20260820-120000",
                    "created_at": "2026-08-20T12:00:00+08:00",
                    "model": {"weights": "yolo11s_best.pt"},
                    "image": {"width": 8, "height": 8},
                    "parameters": {"localization_model": "yolo11s"},
                    "analysis": {
                        "candidate_count": 0,
                        "top_label": "未偵測",
                        "max_confidence": 0.0,
                        "status": "已完成",
                        "student_vlm": {"status": "not_requested"},
                        "ai_assessment": "none",
                    },
                    "detections": [],
                    "report": {"report_status": "草稿"},
                    "artifacts": {"original": "/original", "localized": "/localized", "regions": []},
                }

            def get_image(self, _path):
                return Image.new("RGB", (8, 8))

            def list_analyses(self):
                return []

        api = LocalizationAPI()
        with patch.object(app, "api_for", return_value=api):
            outputs = app.analyze_remote(
                "token",
                Image.new("RGB", (8, 8)),
                0.25,
                0.45,
                100,
                "yolo11s",
                "gemma4",
            )
        self.assertEqual(
            api.calls,
            [{"student_model": app.YOLO_ONLY_STUDENT_MODEL, "localization_model": "yolo11s"}],
        )
        self.assertEqual(len(outputs), 18)
        self.assertIn("未偵測到候選異常區域", outputs[0])
        self.assertFalse(outputs[-2]["interactive"])
        self.assertEqual(outputs[-1], [])

    def test_structured_stage_sends_only_user_selected_indices(self) -> None:
        class RegionAPI:
            def __init__(self) -> None:
                self.request = None

            def analyze_regions(self, case_id, student_model, detection_indices):
                self.request = (case_id, student_model, detection_indices)
                return {
                    "case_id": case_id,
                    "created_at": "2026-08-20T12:00:00+08:00",
                    "model": {"weights": "yolo11m_best.pt"},
                    "image": {"width": 100, "height": 80},
                    "parameters": {"localization_model": "yolo11m"},
                    "analysis": {
                        "candidate_count": 2,
                        "top_label": "candidate",
                        "max_confidence": 0.9,
                        "status": "待複核",
                        "student_vlm": {
                            "status": "completed",
                            "model_name": "Gemma 4 31B",
                            "structured_output": {"ok": True},
                        },
                        "ai_assessment": "selected",
                    },
                    "detections": [
                        {"index": 1, "class_name": "candidate", "confidence": 0.8, "bbox_xyxy": [0, 0, 20, 20]},
                        {"index": 2, "class_name": "candidate", "confidence": 0.9, "bbox_xyxy": [30, 30, 60, 60]},
                    ],
                    "report": {"report_status": "草稿"},
                    "artifacts": {"original": "/original", "localized": "/localized", "regions": ["/roi"]},
                }

            def get_image(self, _path):
                return Image.new("RGB", (100, 80))

            def list_analyses(self):
                return []

        api = RegionAPI()
        with patch.object(app, "api_for", return_value=api):
            outputs = app.analyze_selected_regions(
                "token", "PV-20260820-120000", "gemma4", ["2"]
            )
        self.assertEqual(
            api.request,
            ("PV-20260820-120000", "gemma4", [2]),
        )
        self.assertEqual(len(outputs), 13)
        self.assertIn("結構化分析完成", outputs[0])
        self.assertEqual(outputs[4], {"ok": True})

    def test_independent_region_reports_are_switchable(self) -> None:
        metadata = {
            "case_id": "CASE-MULTI",
            "analysis": {
                "student_vlm": {
                    "status": "completed",
                    "region_reports": [
                        {
                            "region_index": 1,
                            "detection_index": 1,
                            "status": "completed",
                            "model_name": "Gemma4 31B",
                            "structured_output": {
                                "Morphological_Summary": {
                                    "Direct_Observations_Only": "區域一所見"
                                }
                            },
                        },
                        {
                            "region_index": 2,
                            "detection_index": 2,
                            "status": "completed",
                            "model_name": "Gemma4 31B",
                            "structured_output": {
                                "Morphological_Summary": {
                                    "Direct_Observations_Only": "區域二所見"
                                }
                            },
                        },
                    ],
                }
            },
        }
        (
            reports,
            output,
            active,
            rendered,
            model_selector,
            region_selector,
        ) = app.structured_region_view(metadata)
        self.assertEqual(active, "legacy::1")
        self.assertEqual(output["Morphological_Summary"]["Direct_Observations_Only"], "區域一所見")
        self.assertEqual(
            model_selector["choices"],
            [("Gemma4 31B", "legacy")],
        )
        self.assertFalse(model_selector["interactive"])
        self.assertEqual(
            region_selector["choices"],
            [
                ("異常區域 1", "legacy::1"),
                ("異常區域 2", "legacy::2"),
            ],
        )
        self.assertTrue(region_selector["interactive"])
        switched = app.switch_structured_region_report(
            reports, "legacy::2", "CASE-MULTI"
        )
        self.assertEqual(switched[0]["Morphological_Summary"]["Direct_Observations_Only"], "區域二所見")
        self.assertEqual(switched[2], "legacy::2")
        self.assertIn("異常區域 2", switched[1])
        self.assertIn("區域二所見", switched[1])

    def test_two_models_and_two_regions_produce_four_dropdown_reports(self) -> None:
        reports = []
        for model_key, model_name in (
            ("gemma4", "Gemma4 31B"),
            ("mistral-small-3.1", "Mistral Small 3.1 24B"),
        ):
            for detection_index in (1, 2):
                reports.append({
                    "region_index": detection_index,
                    "detection_index": detection_index,
                    "status": "completed",
                    "model_key": model_key,
                    "model_name": model_name,
                    "structured_output": {
                        "Morphological_Summary": {
                            "Direct_Observations_Only": (
                                f"{model_name} 區域 {detection_index} 所見"
                            )
                        }
                    },
                })
        metadata = {
            "case_id": "CASE-TWO-MODELS",
            "analysis": {
                "student_vlm": {
                    "status": "completed",
                    "model_key": "mistral-small-3.1",
                    "model_name": "Mistral Small 3.1 24B",
                    "region_reports": reports,
                }
            },
        }

        (
            stored,
            output,
            active,
            rendered,
            model_selector,
            region_selector,
        ) = app.structured_region_view(metadata, "mistral-small-3.1::2")

        self.assertEqual(len(stored), 4)
        self.assertEqual(active, "mistral-small-3.1::2")
        self.assertEqual(
            model_selector["choices"],
            [
                ("Gemma4 31B", "gemma4"),
                ("Mistral Small 3.1 24B", "mistral-small-3.1"),
            ],
        )
        self.assertEqual(model_selector["value"], "mistral-small-3.1")
        self.assertEqual(
            region_selector["choices"],
            [
                ("異常區域 1", "mistral-small-3.1::1"),
                ("異常區域 2", "mistral-small-3.1::2"),
            ],
        )
        self.assertIn("Mistral Small 3.1 24B 區域 2 所見", rendered)
        self.assertIn("Mistral Small 3.1 24B 區域 2 所見", str(output))

        switched_model = app.switch_structured_report_model(
            stored,
            "gemma4",
            "mistral-small-3.1::2",
            "CASE-TWO-MODELS",
        )
        self.assertEqual(switched_model[2], "gemma4::2")
        self.assertEqual(
            switched_model[3]["choices"],
            [
                ("異常區域 1", "gemma4::1"),
                ("異常區域 2", "gemma4::2"),
            ],
        )
        self.assertIn("Gemma4 31B 區域 2 所見", switched_model[1])

        switched_region = app.switch_structured_region_report(
            stored, "gemma4::1", "CASE-TWO-MODELS"
        )
        self.assertEqual(switched_region[2], "gemma4::1")
        self.assertIn("Gemma4 31B 區域 1 所見", switched_region[1])

    def test_context_menu_has_explicit_actions_without_cell_click_loading(self) -> None:
        self.assertIn("載入這筆紀錄", app.CLIENT_JS)
        self.assertIn("編輯此筆所有欄位", app.CLIENT_JS)
        self.assertIn("刪除整筆紀錄", app.CLIENT_JS)
        self.assertNotIn("firstCell.dispatchEvent", app.CLIENT_JS)

    def test_background_poll_populates_ready_student_models(self) -> None:
        api = Mock()
        api.model.return_value = {
            "default_student_model": "",
            "student_models": [
                {
                    "key": "gemma4",
                    "display_name": "Gemma 4 31B",
                    "parameter_scale": "31B",
                    "inference_ready": True,
                },
                {
                    "key": "mistral-small-3.1",
                    "display_name": "Mistral Small 3.1",
                    "parameter_scale": "24B",
                    "inference_ready": False,
                },
            ],
        }
        with patch.dict(app._CONNECTIONS, {"token": api}, clear=True):
            update = app.poll_student_models("token", None)
        self.assertEqual(update["value"], "gemma4")
        self.assertEqual([value for _label, value in update["choices"]], ["gemma4"])
        self.assertTrue(update["interactive"])

    def test_in_page_model_loading_status_tracks_rest_and_each_vlm(self) -> None:
        waiting = app.structured_model_loading_status("pending-token")
        self.assertIn("0/3 已就緒", waiting)
        self.assertIn("連線建立中", waiting)

        api = Mock()
        api.model.return_value = {
            "student_models": [
                {
                    "key": "mistral-small-3.1",
                    "assets_ready": True,
                    "endpoint_configured": True,
                    "endpoint_ready": True,
                    "inference_ready": True,
                },
                {
                    "key": "gemma4",
                    "assets_ready": True,
                    "endpoint_configured": True,
                    "endpoint_ready": False,
                    "inference_ready": False,
                },
            ]
        }
        with patch.dict(app._CONNECTIONS, {"token": api}, clear=True):
            loading = app.structured_model_loading_status("token")
        self.assertIn("2/3 已就緒", loading)
        self.assertIn("Mistral Small 3.1 24B", loading)
        self.assertIn("Gemma4 31B", loading)
        self.assertIn("載入與暖機中", loading)
        self.assertIn('aria-valuenow="67"', loading)

    def test_case_crud_handlers_use_backend_records(self) -> None:
        class CaseAPI:
            def __init__(self) -> None:
                self.updated = None
                self.deleted = None

            def create_case(self, case_id):
                return {"case_id": case_id or "PV-AUTO"}

            def list_analyses(self):
                return [{
                    "case_id": "CASE-2", "created_at": "now", "image_size": "20 × 20",
                    "candidate_count": 0, "top_label": "未分析", "max_confidence": None,
                    "status": "未分析", "localization_model": "", "student_model": "",
                    "student_vlm_status": "not_requested",
                }]

            def update_case(self, original_case_id, new_case_id, report):
                self.updated = (original_case_id, new_case_id, report)
                return {
                    "case_id": new_case_id,
                    "created_at": "now",
                    "model": {"weights": ""},
                    "analysis": {"student_vlm": {"status": "not_requested"}, "ai_assessment": "manual"},
                    "detections": [],
                    "report": report,
                    "artifacts": {"original": "/original", "localized": "/localized", "regions": []},
                }

            def get_image(self, _path):
                return Image.new("RGB", (20, 20), "white")

            def delete_analysis(self, case_id):
                self.deleted = case_id

        api = CaseAPI()
        with patch.object(app, "api_for", return_value=api):
            created = app.create_case_record("token", "CASE-2")
            saved = app.save_case_record(
                "token", "CASE-1", "CASE-2", "P-1", "S-1", "", "", "", "",
                "", "", "", "", "reviewer", "待複核", "", None,
            )
            deleted = app.delete_case_record("token", "CASE-2")
        self.assertEqual(len(created), 33)
        self.assertEqual(len(saved), 6)
        self.assertEqual(len(deleted), 32)
        self.assertIn("CASE-2", created[1])
        self.assertEqual(saved[0:2], ("CASE-2", "CASE-2"))
        self.assertEqual(api.updated[0:2], ("CASE-1", "CASE-2"))
        self.assertEqual(api.updated[2]["patient_id"], "P-1")
        self.assertEqual(api.deleted, "CASE-2")
        self.assertEqual(deleted[2:4], ("", ""))

    def test_running_allocation_reveals_analysis_page_during_model_loading(self) -> None:
        session = SimpleNamespace(
            token="session-token",
            api_key="api-key",
            proxy_url="socks5h://127.0.0.1:8765",
            node="gpu-node-1",
            server_port=9012,
            local_port=8765,
        )

        def fake_wait(*_args, on_update=None, **_kwargs) -> str:
            on_update("RUNNING · gpu-node-1 · 正在初始化 REST Server／分析推論模型")
            return "http://gpu-node-1:9012"

        with (
            patch.object(app, "get_session", return_value=session),
            patch.object(app, "submit_server_job", return_value="42"),
            patch.object(app, "wait_and_establish_tunnel", side_effect=fake_wait),
            patch.object(app, "PathoVisionAPI", _FakeAPI),
            patch.object(app, "set_mcp_api"),
        ):
            updates = list(
                app.submit_and_connect(
                    "session-token",
                    "/work/project",
                    "8gpus",
                    "",
                    "04:00:00",
                    32,
                    256,
                    3,
                    8765,
                    progress=Mock(),
                )
            )

        self.assertGreaterEqual(len(updates), 3)
        allocation_update = updates[0]
        self.assertEqual(len(allocation_update), 13)
        self.assertFalse(allocation_update[1]["visible"])
        self.assertTrue(allocation_update[2]["visible"])
        self.assertIn("Compute Node 已配置", allocation_update[3])
        self.assertFalse(allocation_update[7]["interactive"])
        self.assertFalse(allocation_update[8]["interactive"])
        self.assertFalse(allocation_update[9]["interactive"])
        self.assertFalse(allocation_update[10]["interactive"])
        self.assertFalse(allocation_update[11]["interactive"])
        self.assertFalse(allocation_update[12]["interactive"])

        ready_update = updates[-1]
        self.assertEqual(ready_update[3], "")
        self.assertFalse(ready_update[1]["visible"])
        self.assertTrue(ready_update[2]["visible"])
        self.assertTrue(ready_update[7]["interactive"])
        self.assertTrue(ready_update[8]["interactive"])
        self.assertTrue(ready_update[9]["interactive"])
        self.assertTrue(ready_update[10]["interactive"])
        self.assertTrue(ready_update[11]["interactive"])
        self.assertTrue(ready_update[12]["interactive"])
        app._CONNECTIONS.clear()


if __name__ == "__main__":
    unittest.main()
