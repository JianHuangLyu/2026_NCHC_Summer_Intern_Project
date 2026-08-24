"""PathoVision REST API server for NCHC NANO4 compute nodes.

The server owns the YOLO model, inference, case storage, and report persistence.
The localhost Gradio client communicates with this service through an SSH tunnel.
"""

from __future__ import annotations

import hmac
import io
import json
import os
import re
import shutil
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field
from PIL import Image, ImageDraw, ImageOps, UnidentifiedImageError
from starlette.concurrency import run_in_threadpool

from student_vlm import (
    StudentAssetsNotReadyError,
    StudentEndpointNotReadyError,
    StudentVLMError,
    UnknownStudentModelError,
    analyze_with_student,
    list_student_models,
    prepare_roi_regions,
    serialize_roi_regions,
    require_student_model,
)

APP_DIR = Path(__file__).resolve().parent
PROJECT_DIR = APP_DIR
MODEL_PATH = Path(
    os.environ.get("PATHOVISION_MODEL_PATH", str(PROJECT_DIR / "Localization_model" / "yolo11m_best.pt"))
).expanduser().resolve()
YOLO11S_MODEL_PATH = Path(
    os.environ.get(
        "PATHOVISION_YOLO11S_MODEL_PATH",
        str(PROJECT_DIR / "Localization_model" / "yolo11s_best.pt"),
    )
).expanduser().resolve()
LOCALIZATION_MODEL_PATHS = {
    "yolo11m": MODEL_PATH,
    "yolo11s": YOLO11S_MODEL_PATH,
}
LOCALIZATION_MODEL_DISPLAY_NAMES = {
    "yolo11m": "YOLO11m",
    "yolo11s": "YOLO11s",
}
CASE_ROOT = Path(
    os.environ.get("PATHOVISION_CASE_ROOT", str(PROJECT_DIR / ".pathovision_server" / "cases"))
).expanduser().resolve()
API_KEY = os.environ.get("PATHOVISION_API_KEY", "").strip()
MAX_UPLOAD_BYTES = int(os.environ.get("PATHOVISION_MAX_UPLOAD_MB", "100")) * 1024 * 1024
MAX_SELECTED_VLM_REGIONS = max(
    1, int(os.environ.get("PATHOVISION_MAX_VLM_ROIS", "4"))
)
DEVICE = os.environ.get("PATHOVISION_DEVICE", "").strip() or None
YOLO_HALF = os.environ.get("PATHOVISION_YOLO_HALF", "0").strip().lower() in {
    "1", "true", "yes", "on"
}
DEFAULT_LOCALIZATION_MODEL = os.environ.get(
    "PATHOVISION_DEFAULT_LOCALIZATION_MODEL", "yolo11m"
).strip() or "yolo11m"
DEFAULT_STUDENT_MODEL = os.environ.get(
    "PATHOVISION_DEFAULT_STUDENT_MODEL",
    "",
).strip()
YOLO_ONLY_STUDENT_VALUES = {
    "__yolo_only__",
    "yolo-only",
    "yolo_only",
    "none",
}
STUDENT_MODEL_ROOT = Path(
    os.environ.get(
        "PATHOVISION_STUDENT_MODEL_ROOT",
        str(PROJECT_DIR / "Student_model"),
    )
).expanduser().resolve()
CASE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

CASE_ROOT.mkdir(parents=True, exist_ok=True, mode=0o750)

_models: dict[str, Any] = {}
_model_load_errors: dict[str, str] = {}
_model_lock = threading.Lock()
_inference_lock = threading.Lock()
_case_mutation_lock = threading.RLock()
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


class Detection(BaseModel):
    index: int
    class_id: int
    class_name: str
    confidence: float
    bbox_xyxy: list[float] = Field(min_length=4, max_length=4)


class ArtifactLinks(BaseModel):
    original: str
    localized: str
    regions: list[str] = Field(default_factory=list)


class AnalysisRecord(BaseModel):
    schema_version: int = 1
    case_id: str
    created_at: str
    model: dict[str, Any]
    image: dict[str, Any]
    parameters: dict[str, Any]
    analysis: dict[str, Any]
    detections: list[Detection]
    report: dict[str, Any] = Field(default_factory=dict)
    artifacts: ArtifactLinks


class AnalysisListItem(BaseModel):
    case_id: str
    created_at: str
    image_size: str
    candidate_count: int
    top_label: str
    max_confidence: float | None
    status: str
    localization_model: str = ""
    student_model: str = ""
    student_vlm_status: str = "not_requested"


class StudentRegionAnalysisRequest(BaseModel):
    student_model: str = Field(min_length=1)
    detection_indices: list[int] = Field(min_length=1, max_length=32)


class ReportUpdate(BaseModel):
    patient_id: str = ""
    specimen_id: str = ""
    collection_date: str = ""
    specimen_type: str = ""
    anatomical_site: str = ""
    stain: str = ""
    microscopic_findings: str = ""
    ai_summary: str = ""
    final_diagnosis: str = ""
    notes: str = ""
    reviewer: str = ""
    report_status: str = "草稿"
    signed_at: str = ""


class CaseCreateRequest(BaseModel):
    case_id: str = Field(default="", max_length=64)


class CaseUpdateRequest(BaseModel):
    case_id: str = Field(min_length=1, max_length=64)
    report: ReportUpdate


class StructuredAnalysisUpdate(BaseModel):
    structured_output: dict[str, Any]
    detection_index: int | None = None


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str
    model_exists: bool
    model_loaded: bool


def require_api_key(received: Annotated[str | None, Depends(_api_key_header)]) -> None:
    if not API_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PATHOVISION_API_KEY is not configured on the server.",
        )
    if received is None or not hmac.compare_digest(received, API_KEY):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key.",
            headers={"WWW-Authenticate": "APIKey"},
        )


def get_model(model_key: str = DEFAULT_LOCALIZATION_MODEL) -> Any:
    """Lazy-load and cache the selected YOLO localization model."""
    model_path = LOCALIZATION_MODEL_PATHS.get(model_key)
    if model_path is None:
        choices = ", ".join(LOCALIZATION_MODEL_PATHS)
        raise RuntimeError(f"Unknown localization model {model_key!r}; choose: {choices}.")
    if model_key not in _models:
        with _model_lock:
            if model_key not in _models:
                if not model_path.is_file():
                    raise RuntimeError(f"Model weights not found: {model_path}")
                try:
                    from ultralytics import YOLO
                except ImportError as exc:
                    raise RuntimeError("ultralytics is not installed on the server.") from exc
                try:
                    _models[model_key] = YOLO(str(model_path))
                    _model_load_errors.pop(model_key, None)
                except Exception as exc:
                    _model_load_errors[model_key] = str(exc)
                    raise
    return _models[model_key]


def warm_localization_models() -> None:
    """Warm both YOLO variants in the background so the first request is fast."""
    warmup_image = Image.new("RGB", (640, 640), "white")
    for model_key in LOCALIZATION_MODEL_PATHS:
        try:
            model = get_model(model_key)
            predict_kwargs: dict[str, Any] = {
                "source": warmup_image,
                "conf": 0.95,
                "max_det": 1,
                "verbose": False,
            }
            if DEVICE:
                predict_kwargs["device"] = DEVICE
            if YOLO_HALF and DEVICE and str(DEVICE).lower() != "cpu":
                predict_kwargs["quantize"] = 16
            with _inference_lock:
                model.predict(**predict_kwargs)
        except Exception as exc:
            _model_load_errors[model_key] = str(exc)


def start_localization_warmup() -> None:
    enabled = os.environ.get(
        "PATHOVISION_PRELOAD_LOCALIZATION_MODELS", "0"
    ).strip().lower() not in {"0", "false", "no"}
    if enabled:
        threading.Thread(
            target=warm_localization_models,
            name="pathovision-yolo-warmup",
            daemon=True,
        ).start()


def class_name(names: Any, class_id: int) -> str:
    if isinstance(names, dict):
        return str(names.get(class_id, class_id))
    if isinstance(names, (list, tuple)) and 0 <= class_id < len(names):
        return str(names[class_id])
    return str(class_id)


def draw_detection(image: Image.Image, box: list[float]) -> None:
    draw = ImageDraw.Draw(image)
    width, height = image.size
    x1, y1, x2, y2 = box
    x1 = max(0, min(int(round(x1)), width - 1))
    y1 = max(0, min(int(round(y1)), height - 1))
    x2 = max(0, min(int(round(x2)), width - 1))
    y2 = max(0, min(int(round(y2)), height - 1))
    line_width = max(2, round(min(width, height) / 250))
    draw.rectangle((x1, y1, x2, y2), outline=(230, 30, 45), width=line_width)


def generate_case_id(now: datetime) -> str:
    base_id = now.strftime("PV-%Y%m%d-%H%M%S")
    if not (CASE_ROOT / base_id).exists():
        return base_id
    for suffix in range(1, 100):
        candidate = f"{base_id}-{suffix:02d}"
        if not (CASE_ROOT / candidate).exists():
            return candidate
    raise RuntimeError("Too many cases were created in the same second.")


def case_directory(case_id: str) -> Path:
    if not CASE_ID_PATTERN.fullmatch(case_id):
        raise HTTPException(status_code=400, detail="Invalid case ID.")
    path = (CASE_ROOT / case_id).resolve()
    if path.parent != CASE_ROOT:
        raise HTTPException(status_code=400, detail="Invalid case path.")
    return path


def artifact_url(case_id: str, kind: str) -> str:
    return f"/api/v1/analyses/{case_id}/images/{kind}"


def load_metadata(case_id: str) -> dict[str, Any]:
    path = case_directory(case_id) / "analysis.json"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Case not found.")
    try:
        with path.open("r", encoding="utf-8") as file:
            metadata = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail="Stored case metadata is unreadable.") from exc
    if metadata.get("case_id") != case_id:
        raise HTTPException(status_code=500, detail="Stored case metadata is inconsistent.")
    stored_regions = metadata.get("files", {}).get("regions", [])
    region_links = [
        f"/api/v1/analyses/{case_id}/images/regions/{index}"
        for index, filename in enumerate(stored_regions, start=1)
        if isinstance(filename, str) and Path(filename).name == filename
    ]
    metadata["artifacts"] = {
        "original": artifact_url(case_id, "original"),
        "localized": artifact_url(case_id, "localized"),
        "regions": region_links,
    }
    return metadata


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
    os.chmod(temp, 0o640)
    temp.replace(path)


def normalize_case_id(value: str) -> str:
    case_id = value.strip()
    if not CASE_ID_PATTERN.fullmatch(case_id):
        raise HTTPException(
            status_code=422,
            detail=(
                "Case ID must be 1-64 characters, start with a letter or number, "
                "and contain only letters, numbers, dot, underscore, or hyphen."
            ),
        )
    return case_id


def update_case_record_data(
    case_id: str,
    new_case_id: str,
    report: ReportUpdate,
) -> dict[str, Any]:
    """Atomically update report data and, when requested, rename its case directory."""
    with _case_mutation_lock:
        source_id = normalize_case_id(case_id)
        target_id = normalize_case_id(new_case_id)
        source_dir = case_directory(source_id)
        if source_dir.is_symlink() or not source_dir.is_dir():
            raise HTTPException(status_code=404, detail="Case not found.")
        metadata = load_metadata(source_id)
        metadata.pop("artifacts", None)
        metadata["case_id"] = target_id
        metadata["report"] = report.model_dump()

        if target_id == source_id:
            atomic_write_json(source_dir / "analysis.json", metadata)
            return load_metadata(source_id)

        target_dir = case_directory(target_id)
        if target_dir.exists() or target_dir.is_symlink():
            raise HTTPException(status_code=409, detail="The requested case ID already exists.")
        try:
            source_dir.rename(target_dir)
            atomic_write_json(target_dir / "analysis.json", metadata)
        except OSError as exc:
            if target_dir.is_dir() and not source_dir.exists():
                try:
                    target_dir.rename(source_dir)
                except OSError:
                    pass
            raise HTTPException(status_code=500, detail=f"Unable to rename case: {exc}") from exc
        return load_metadata(target_id)


def run_inference(
    image: Image.Image,
    confidence: float,
    iou: float,
    max_detections: int,
    localization_model: str = DEFAULT_LOCALIZATION_MODEL,
) -> tuple[Image.Image, list[dict[str, Any]]]:
    model = get_model(localization_model)
    normalized = ImageOps.exif_transpose(image).convert("RGB")
    predict_kwargs: dict[str, Any] = {
        "source": normalized,
        "conf": confidence,
        "iou": iou,
        "max_det": max_detections,
        "verbose": False,
    }
    if DEVICE:
        predict_kwargs["device"] = DEVICE
    if YOLO_HALF and DEVICE and str(DEVICE).lower() != "cpu":
        predict_kwargs["quantize"] = 16

    with _inference_lock:
        results = model.predict(**predict_kwargs)

    result = results[0]
    localized = normalized.copy()
    detections: list[dict[str, Any]] = []
    if result.boxes is not None:
        coordinates = result.boxes.xyxy.detach().cpu().tolist()
        scores = result.boxes.conf.detach().cpu().tolist()
        class_ids = result.boxes.cls.detach().cpu().tolist()
        for index, (box_raw, score, class_id_raw) in enumerate(
            zip(coordinates, scores, class_ids), start=1
        ):
            class_id = int(class_id_raw)
            box = [round(float(value), 2) for value in box_raw]
            draw_detection(localized, box)
            detections.append(
                {
                    "index": index,
                    "class_id": class_id,
                    "class_name": class_name(result.names, class_id),
                    "confidence": round(float(score), 6),
                    "bbox_xyxy": box,
                }
            )
    return localized, detections


def create_case(
    original: Image.Image,
    localized: Image.Image,
    detections: list[dict[str, Any]],
    confidence: float,
    iou: float,
    max_detections: int,
    student_vlm: dict[str, Any] | None = None,
    roi_regions: list[dict[str, Any]] | None = None,
    localization_model: str = DEFAULT_LOCALIZATION_MODEL,
) -> dict[str, Any]:
    now = datetime.now().astimezone()
    created_at = now.isoformat(timespec="seconds")
    case_id = generate_case_id(now)
    case_dir = case_directory(case_id)
    case_dir.mkdir(mode=0o750)

    original_path = case_dir / "original.png"
    localized_path = case_dir / "localized.png"
    metadata_path = case_dir / "analysis.json"

    original.convert("RGB").save(original_path, format="PNG", compress_level=3)
    localized.convert("RGB").save(localized_path, format="PNG", compress_level=3)
    os.chmod(original_path, 0o640)
    os.chmod(localized_path, 0o640)

    regions = list(roi_regions or [])
    region_files: list[str] = []
    for region_index, region in enumerate(regions, start=1):
        crop = region.get("image")
        if not isinstance(crop, Image.Image):
            continue
        filename = f"roi_{region_index:03d}.png"
        crop_path = case_dir / filename
        crop.convert("RGB").save(crop_path, format="PNG", compress_level=3)
        os.chmod(crop_path, 0o640)
        region_files.append(filename)

    label_counts = Counter(item["class_name"] for item in detections)
    top_label = label_counts.most_common(1)[0][0] if label_counts else "未偵測"
    max_confidence = max((item["confidence"] for item in detections), default=0.0)
    width, height = original.size
    count = len(detections)
    localization_summary = (
        f"AI 模型標示 {count} 個候選異常區域；主要辨識類別為「{top_label}」，"
        f"最高信心分數為 {max_confidence:.1%}。請結合原始影像與臨床資料人工複核。"
        if count
        else "目前信心門檻下未偵測到候選異常區域，仍建議由專業人員完整檢視原始影像。"
    )
    vlm_result = dict(student_vlm or {"status": "not_requested"})
    vlm_status = str(vlm_result.get("status", "not_requested"))
    morphology_summary = str(vlm_result.get("summary", "")).strip()
    if vlm_status == "completed" and morphology_summary:
        ai_summary = (
            localization_summary
            + "\n\n"
            + f"{vlm_result.get('model_name', '分析推論模型')} 形態分析：{morphology_summary}"
        )
    elif vlm_status == "failed":
        ai_summary = localization_summary + "\n\n分析推論模型未完成，請查看執行狀態後重試。"
    else:
        ai_summary = localization_summary

    files: dict[str, Any] = {
        "original": original_path.name,
        "localized": localized_path.name,
        "regions": region_files,
    }
    if vlm_status == "completed" and isinstance(vlm_result.get("structured_output"), dict):
        vlm_path = case_dir / "student_vlm_analysis.json"
        atomic_write_json(vlm_path, vlm_result["structured_output"])
        files["student_vlm"] = vlm_path.name

    metadata: dict[str, Any] = {
        "schema_version": 3,
        "case_id": case_id,
        "created_at": created_at,
        "model": {
            "key": localization_model,
            "name": LOCALIZATION_MODEL_DISPLAY_NAMES[localization_model],
            "weights": LOCALIZATION_MODEL_PATHS[localization_model].name,
            "student_vlm": {
                "key": vlm_result.get("model_key", ""),
                "name": vlm_result.get("model_name", ""),
                "model_id": vlm_result.get("model_id", ""),
            },
        },
        "files": files,
        "image": {"width": width, "height": height, "mode": "RGB"},
        "parameters": {
            "localization_model": localization_model,
            "confidence": confidence,
            "iou": iou,
            "max_detections": max_detections,
            "student_model": vlm_result.get("model_key", ""),
        },
        "analysis": {
            "candidate_count": count,
            "top_label": top_label,
            "max_confidence": max_confidence,
            "status": "待複核" if count or vlm_status == "completed" else "已完成",
            "localization_assessment": localization_summary,
            "ai_assessment": ai_summary,
            "label_distribution": dict(label_counts),
            "student_vlm": vlm_result,
            "vlm_regions": serialize_roi_regions(regions),
        },
        "detections": detections,
        "report": {"ai_summary": ai_summary, "report_status": "草稿"},
    }
    atomic_write_json(metadata_path, metadata)
    metadata["artifacts"] = {
        "original": artifact_url(case_id, "original"),
        "localized": artifact_url(case_id, "localized"),
        "regions": [
            f"/api/v1/analyses/{case_id}/images/regions/{index}"
            for index in range(1, len(region_files) + 1)
        ],
    }
    return metadata


def analysis_list_item(metadata: dict[str, Any]) -> AnalysisListItem:
    image = metadata.get("image", {})
    analysis = metadata.get("analysis", {})
    student_vlm = analysis.get("student_vlm", {})
    count = int(analysis.get("candidate_count", 0))
    return AnalysisListItem(
        case_id=str(metadata.get("case_id", "")),
        created_at=str(metadata.get("created_at", "")),
        image_size=f"{int(image.get('width', 0))} × {int(image.get('height', 0))}",
        candidate_count=count,
        top_label=str(analysis.get("top_label", "未偵測")),
        max_confidence=float(analysis.get("max_confidence", 0.0)) if count else None,
        status=str(analysis.get("status", "已完成")),
        localization_model=str(metadata.get("model", {}).get("name", "")),
        student_model=str(student_vlm.get("model_name", "")),
        student_vlm_status=str(student_vlm.get("status", "not_requested")),
    )


app = FastAPI(
    title="PathoVision NANO4 REST API",
    version="2.3.0",
    description="Selectable YOLO localization plus user-selected ROI analysis-inference model.",
)


@app.on_event("startup")
def _startup() -> None:
    start_localization_warmup()


@app.get("/healthz", response_model=HealthResponse, include_in_schema=False)
def healthz() -> HealthResponse:
    return HealthResponse(
        status="ok",
        service="pathovision-api",
        model_exists=LOCALIZATION_MODEL_PATHS.get(
            DEFAULT_LOCALIZATION_MODEL, MODEL_PATH
        ).is_file(),
        model_loaded=DEFAULT_LOCALIZATION_MODEL in _models,
    )


@app.get("/api/v1/model", dependencies=[Depends(require_api_key)])
def model_info() -> dict[str, Any]:
    default_path = LOCALIZATION_MODEL_PATHS.get(DEFAULT_LOCALIZATION_MODEL, MODEL_PATH)
    localization_models = [
        {
            "key": key,
            "display_name": LOCALIZATION_MODEL_DISPLAY_NAMES[key],
            "weights": path.name,
            "ready": path.is_file(),
            "loaded": key in _models,
            "load_error": _model_load_errors.get(key, ""),
        }
        for key, path in LOCALIZATION_MODEL_PATHS.items()
    ]
    return {
        "key": DEFAULT_LOCALIZATION_MODEL,
        "name": LOCALIZATION_MODEL_DISPLAY_NAMES.get(
            DEFAULT_LOCALIZATION_MODEL, DEFAULT_LOCALIZATION_MODEL
        ),
        "weights": default_path.name,
        "weights_exists": default_path.is_file(),
        "localization_models": localization_models,
        "loaded": DEFAULT_LOCALIZATION_MODEL in _models,
        "device": DEVICE or "auto",
        "yolo_half_precision": YOLO_HALF and bool(DEVICE) and str(DEVICE).lower() != "cpu",
        "default_student_model": DEFAULT_STUDENT_MODEL,
        "max_selected_vlm_regions": MAX_SELECTED_VLM_REGIONS,
        "student_models": list_student_models(STUDENT_MODEL_ROOT),
    }


@app.get("/api/v1/student-models", dependencies=[Depends(require_api_key)])
def student_models() -> list[dict[str, Any]]:
    """Return UI-safe analysis-inference model readiness and bundle metadata."""
    return list_student_models(STUDENT_MODEL_ROOT)


@app.post("/api/v1/student-analysis", dependencies=[Depends(require_api_key)])
async def create_student_analysis(
    image: Annotated[UploadFile, File(description="Unannotated pathology image")],
    student_model: Annotated[str, Form()],
    detections_json: Annotated[str, Form()] = "[]",
) -> dict[str, Any]:
    """Run only the analysis-inference stage for UIs that already perform YOLO locally."""
    model_key = student_model.strip()
    try:
        student_spec, _student_status = require_student_model(STUDENT_MODEL_ROOT, model_key)
    except UnknownStudentModelError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except StudentAssetsNotReadyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except StudentEndpointNotReadyError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if image.content_type and not image.content_type.startswith("image/"):
        raise HTTPException(status_code=415, detail="Only image uploads are accepted.")
    data = await image.read(MAX_UPLOAD_BYTES + 1)
    await image.close()
    if not data:
        raise HTTPException(status_code=400, detail="The uploaded image is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="The uploaded image is too large.")
    try:
        raw_detections = json.loads(detections_json)
        if not isinstance(raw_detections, list):
            raise ValueError("detections_json must contain a list.")
        detections = [item for item in raw_detections if isinstance(item, dict)]
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        with Image.open(io.BytesIO(data)) as opened:
            original = ImageOps.exif_transpose(opened).convert("RGB").copy()
        regions = prepare_roi_regions(
            original,
            detections,
            max_regions=min(len(detections), MAX_SELECTED_VLM_REGIONS),
        )
        if not regions:
            raise HTTPException(
                status_code=422,
                detail="At least one valid YOLO region is required for Student analysis.",
            )
        return await run_in_threadpool(
            analyze_with_student,
            root=STUDENT_MODEL_ROOT,
            model_key=model_key,
            image=original,
            detections=detections,
            roi_regions=regions,
            validated_spec=student_spec,
        )
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="The uploaded file is not a valid image.") from exc
    except StudentVLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post(
    "/api/v1/analyses",
    response_model=AnalysisRecord,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_api_key)],
)
async def create_analysis(
    image: Annotated[UploadFile, File(description="Medical image to analyze")],
    confidence: Annotated[float, Form(ge=0.05, le=0.95)] = 0.25,
    iou: Annotated[float, Form(ge=0.10, le=0.90)] = 0.45,
    max_detections: Annotated[int, Form(ge=1, le=300)] = 100,
    localization_model: Annotated[str, Form()] = DEFAULT_LOCALIZATION_MODEL,
    student_model: Annotated[str, Form()] = "",
) -> dict[str, Any]:
    localization_model = localization_model.strip()
    if localization_model not in LOCALIZATION_MODEL_PATHS:
        choices = ", ".join(LOCALIZATION_MODEL_PATHS)
        raise HTTPException(
            status_code=422,
            detail=f"Unknown localization model {localization_model!r}; choose: {choices}.",
        )
    if not LOCALIZATION_MODEL_PATHS[localization_model].is_file():
        raise HTTPException(
            status_code=503,
            detail=f"Localization model weights not found: {LOCALIZATION_MODEL_PATHS[localization_model].name}.",
        )
    student_model = student_model.strip()
    if student_model.lower() in YOLO_ONLY_STUDENT_VALUES:
        student_model = ""
    if student_model:
        raise HTTPException(
            status_code=422,
            detail=(
                "POST /api/v1/analyses performs YOLO localization only. "
                "After reviewing detections, submit the selected detection indices to "
                "POST /api/v1/analyses/{case_id}/student-analysis."
            ),
        )

    if image.content_type and not image.content_type.startswith("image/"):
        raise HTTPException(status_code=415, detail="Only image uploads are accepted.")

    data = await image.read(MAX_UPLOAD_BYTES + 1)
    await image.close()
    if not data:
        raise HTTPException(status_code=400, detail="The uploaded image is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="The uploaded image is too large.")

    try:
        with Image.open(io.BytesIO(data)) as opened:
            original = ImageOps.exif_transpose(opened).convert("RGB").copy()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="The uploaded file is not a valid image.") from exc

    try:
        localized, detections = await run_in_threadpool(
            run_inference,
            original,
            confidence,
            iou,
            max_detections,
            localization_model,
        )
        # Stage 1 never invokes an analysis-inference model. The user selects one or
        # more detection indices before the structured-analysis endpoint is called.
        roi_regions: list[dict[str, Any]] = []
        return create_case(
            original,
            localized,
            detections,
            confidence,
            iou,
            max_detections,
            None,
            roi_regions,
            localization_model,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Inference failed: {exc}") from exc


def update_case_student_analysis(
    metadata: dict[str, Any],
    original: Image.Image,
    student_result: dict[str, Any],
    roi_regions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Persist one model run without discarding other models' ROI reports."""
    case_id = str(metadata["case_id"])
    # VLM calls can finish at different times. Reload while holding the case
    # lock so a later model never writes an older metadata snapshot over a run
    # that has already completed.
    with _case_mutation_lock:
        metadata = load_metadata(case_id)
        metadata.pop("artifacts", None)
        analysis = metadata.setdefault("analysis", {})
        existing_vlm = analysis.get("student_vlm", {})
        student_result = merge_student_region_reports(existing_vlm, student_result)
        return _persist_case_student_analysis(
            metadata, original, student_result, roi_regions
        )


def _student_report_identity(
    report: dict[str, Any], fallback_model_key: str = ""
) -> tuple[str, int] | None:
    model_key = str(report.get("model_key") or fallback_model_key).strip()
    try:
        detection_index = int(
            report.get("detection_index", report.get("region_index"))
        )
    except (TypeError, ValueError):
        return None
    if not model_key or detection_index < 1:
        return None
    return model_key, detection_index


def _student_report_filename(report: dict[str, Any]) -> str | None:
    identity = _student_report_identity(report)
    if identity is None:
        return None
    model_key, detection_index = identity
    safe_model_key = re.sub(r"[^A-Za-z0-9_.-]+", "-", model_key).strip("-.")
    if not safe_model_key:
        return None
    return f"student_vlm_{safe_model_key}_region_{detection_index:03d}.json"


def merge_student_region_reports(
    existing_vlm: Any, latest_result: dict[str, Any]
) -> dict[str, Any]:
    """Merge reports by (model, YOLO detection), replacing only rerun pairs."""
    result = dict(latest_result)
    latest_model_key = str(result.get("model_key", "")).strip()
    latest_model_name = str(result.get("model_name", "")).strip()
    latest_model_id = str(result.get("model_id", "")).strip()

    existing_reports = (
        existing_vlm.get("region_reports", [])
        if isinstance(existing_vlm, dict)
        else []
    )
    existing_model_key = (
        str(existing_vlm.get("model_key", "")).strip()
        if isinstance(existing_vlm, dict)
        else ""
    )
    existing_model_name = (
        str(existing_vlm.get("model_name", "")).strip()
        if isinstance(existing_vlm, dict)
        else ""
    )
    existing_model_id = (
        str(existing_vlm.get("model_id", "")).strip()
        if isinstance(existing_vlm, dict)
        else ""
    )
    merged: list[dict[str, Any]] = []
    for raw_report in existing_reports:
        if not isinstance(raw_report, dict):
            continue
        report = dict(raw_report)
        if existing_model_key:
            report.setdefault("model_key", existing_model_key)
        if existing_model_name:
            report.setdefault("model_name", existing_model_name)
        if existing_model_id:
            report.setdefault("model_id", existing_model_id)
        merged.append(report)
    positions = {
        identity: index
        for index, report in enumerate(merged)
        if (identity := _student_report_identity(
            report,
            str(existing_vlm.get("model_key", ""))
            if isinstance(existing_vlm, dict)
            else "",
        )) is not None
    }

    latest_reports = result.get("region_reports", [])
    if not isinstance(latest_reports, list):
        latest_reports = []
    last_run_reports: list[dict[str, Any]] = []
    for raw_report in latest_reports:
        if not isinstance(raw_report, dict):
            continue
        report = dict(raw_report)
        report.setdefault("model_key", latest_model_key)
        report.setdefault("model_name", latest_model_name)
        report.setdefault("model_id", latest_model_id)
        identity = _student_report_identity(report, latest_model_key)
        if identity is None:
            continue
        report["model_key"] = identity[0]
        last_run_reports.append(report)
        if identity in positions:
            merged[positions[identity]] = report
        else:
            positions[identity] = len(merged)
            merged.append(report)

    last_run_status = str(result.get("status", "failed"))
    result["last_run_status"] = last_run_status
    result["last_run_report_count"] = len(last_run_reports)
    if result.get("error"):
        result["last_run_error"] = result["error"]
    else:
        result.pop("last_run_error", None)
    result["region_reports"] = merged

    completed_reports = [
        report
        for report in merged
        if report.get("status") == "completed"
        and isinstance(report.get("structured_output"), dict)
    ]
    if merged and len(completed_reports) == len(merged):
        result["status"] = "completed"
    elif completed_reports:
        result["status"] = "partial"
    else:
        result["status"] = "failed"

    models: list[dict[str, str]] = []
    seen_models: set[str] = set()
    summaries: list[str] = []
    for report in merged:
        model_key = str(report.get("model_key", "")).strip()
        model_name = str(report.get("model_name", model_key)).strip() or model_key
        if model_key and model_key not in seen_models:
            seen_models.add(model_key)
            models.append({
                "key": model_key,
                "name": model_name,
                "model_id": str(report.get("model_id", "")),
            })
        summary = str(report.get("summary", "")).strip()
        if summary:
            detection_index = report.get(
                "detection_index", report.get("region_index", "?")
            )
            summaries.append(
                f"{model_name or '分析推論模型'} · 異常區域 {detection_index}：{summary}"
            )
    result["models"] = models
    result["model_names"] = [model["name"] for model in models if model["name"]]
    result["summary"] = "\n".join(summaries)
    if completed_reports and not isinstance(result.get("structured_output"), dict):
        result["structured_output"] = completed_reports[0]["structured_output"]

    failures = [
        f"{report.get('model_name', report.get('model_key', '分析推論模型'))} · "
        f"異常區域 {report.get('detection_index', report.get('region_index', '?'))}："
        f"{report.get('error', report.get('status'))}"
        for report in merged
        if report.get("status") != "completed"
    ]
    if failures:
        result["error"] = "；".join(failures)
    else:
        result.pop("error", None)
    return result


def _persist_case_student_analysis(
    metadata: dict[str, Any],
    original: Image.Image,
    student_result: dict[str, Any],
    roi_regions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Write a merged multi-model result while the case lock is held."""
    case_id = str(metadata["case_id"])
    case_dir = case_directory(case_id)
    files = metadata.setdefault("files", {})
    previous_region_files = {
        Path(str(filename)).name
        for filename in files.get("regions", [])
        if re.fullmatch(r"roi_\d{3}\.png", Path(str(filename)).name)
    }
    region_files: list[str] = []
    for region_index, region in enumerate(roi_regions, start=1):
        filename = f"roi_{region_index:03d}.png"
        crop_path = case_dir / filename
        region["image"].convert("RGB").save(crop_path, format="PNG", compress_level=3)
        os.chmod(crop_path, 0o640)
        region_files.append(filename)

    for obsolete_name in previous_region_files.difference(region_files):
        obsolete_path = case_dir / obsolete_name
        if obsolete_path.is_file() and not obsolete_path.is_symlink():
            obsolete_path.unlink()
    files["regions"] = region_files

    previous_report_files = {
        Path(str(filename)).name
        for filename in files.get("student_vlm_regions", [])
        if re.fullmatch(
            r"student_vlm_(?:[A-Za-z0-9_.-]+_)?region_\d{3}\.json",
            Path(str(filename)).name,
        )
    }
    region_report_files: list[str] = []
    region_reports = student_result.get("region_reports", [])
    if isinstance(region_reports, list):
        for report in region_reports:
            if not isinstance(report, dict) or report.get("status") != "completed":
                continue
            structured_output = report.get("structured_output")
            if not isinstance(structured_output, dict):
                continue
            filename = _student_report_filename(report)
            if not filename:
                continue
            atomic_write_json(case_dir / filename, structured_output)
            report["structured_output_file"] = filename
            region_report_files.append(filename)
    for obsolete_name in previous_report_files.difference(region_report_files):
        obsolete_path = case_dir / obsolete_name
        if obsolete_path.is_file() and not obsolete_path.is_symlink():
            obsolete_path.unlink()
    if region_report_files:
        files["student_vlm_regions"] = region_report_files
    else:
        files.pop("student_vlm_regions", None)

    compatibility_output = student_result.get("structured_output")
    if isinstance(compatibility_output, dict):
        vlm_path = case_dir / "student_vlm_analysis.json"
        atomic_write_json(vlm_path, compatibility_output)
        files["student_vlm"] = vlm_path.name
    else:
        files.pop("student_vlm", None)

    model_info = metadata.setdefault("model", {})
    model_info["student_vlm"] = {
        "key": student_result.get("model_key", ""),
        "name": student_result.get("model_name", ""),
        "model_id": student_result.get("model_id", ""),
    }
    model_info["student_vlms"] = student_result.get("models", [])
    parameters = metadata.setdefault("parameters", {})
    parameters["student_model"] = student_result.get("model_key", "")
    parameters["student_models"] = [
        item.get("key", "")
        for item in student_result.get("models", [])
        if isinstance(item, dict) and item.get("key")
    ]
    parameters["selected_detection_indices"] = [
        region.get("detection_index") for region in roi_regions
    ]

    analysis = metadata.setdefault("analysis", {})
    old_ai_summary = str(analysis.get("ai_assessment", ""))
    localization_summary = str(analysis.get("localization_assessment", "")).strip()
    morphology_summary = str(student_result.get("summary", "")).strip()
    if student_result.get("status") in {"completed", "partial"} and morphology_summary:
        ai_summary = (
            localization_summary
            + "\n\n"
            + f"{student_result.get('model_name', '分析推論模型')} 各異常區域形態分析：\n"
            + morphology_summary
        )
        if student_result.get("status") == "partial":
            ai_summary += "\n部分異常區域未完成，請查看各區域報告狀態後重試。"
    else:
        ai_summary = localization_summary + "\n\n所選區域的分析推論模型未完成，請查看錯誤後重試。"
    analysis["student_vlm"] = student_result
    analysis["vlm_regions"] = serialize_roi_regions(roi_regions)
    analysis["ai_assessment"] = ai_summary
    analysis["status"] = "待複核"

    report = metadata.setdefault("report", {})
    if not report.get("ai_summary") or report.get("ai_summary") == old_ai_summary:
        report["ai_summary"] = ai_summary
    atomic_write_json(case_dir / "analysis.json", metadata)
    return load_metadata(case_id)


@app.post(
    "/api/v1/analyses/{case_id}/student-analysis",
    response_model=AnalysisRecord,
    dependencies=[Depends(require_api_key)],
)
def analyze_selected_case_regions(
    case_id: str,
    request: StudentRegionAnalysisRequest,
) -> dict[str, Any]:
    """Analyze only user-selected YOLO detections from an existing case."""
    model_key = request.student_model.strip()
    if model_key.lower() in YOLO_ONLY_STUDENT_VALUES:
        raise HTTPException(status_code=422, detail="請選擇分析推論模型後再進行 ROI 分析。")
    try:
        student_spec, _student_status = require_student_model(STUDENT_MODEL_ROOT, model_key)
    except UnknownStudentModelError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except StudentAssetsNotReadyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except StudentEndpointNotReadyError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    selected_indices = list(dict.fromkeys(request.detection_indices))
    if len(selected_indices) > MAX_SELECTED_VLM_REGIONS:
        raise HTTPException(
            status_code=422,
            detail=f"Select at most {MAX_SELECTED_VLM_REGIONS} regions per analysis.",
        )
    metadata = load_metadata(case_id)
    detections = metadata.get("detections", [])
    detection_by_index = {
        int(item.get("index")): item
        for item in detections
        if isinstance(item, dict) and isinstance(item.get("index"), int)
    }
    missing = [index for index in selected_indices if index not in detection_by_index]
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Selected detection indices do not exist in this case: {missing}.",
        )
    selected_detections = [detection_by_index[index] for index in selected_indices]
    if not selected_detections:
        raise HTTPException(
            status_code=422,
            detail="No YOLO abnormal region was selected; Student analysis was not run.",
        )

    original_name = Path(str(metadata.get("files", {}).get("original", "original.png"))).name
    original_path = case_directory(case_id) / original_name
    try:
        with Image.open(original_path) as opened:
            original = opened.convert("RGB").copy()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise HTTPException(status_code=500, detail="Stored original image is unreadable.") from exc
    roi_regions = prepare_roi_regions(
        original,
        selected_detections,
        max_regions=len(selected_detections),
    )
    if len(roi_regions) != len(selected_detections):
        raise HTTPException(status_code=422, detail="One or more selected YOLO boxes are invalid.")
    localization_model = str(
        metadata.get("parameters", {}).get("localization_model")
        or metadata.get("model", {}).get("key")
        or DEFAULT_LOCALIZATION_MODEL
    )
    serialized_regions = serialize_roi_regions(roi_regions)

    def analyze_one_region(region: dict[str, Any]) -> dict[str, Any]:
        detection_index = int(region["detection_index"])
        detection = detection_by_index[detection_index]
        try:
            result = analyze_with_student(
                root=STUDENT_MODEL_ROOT,
                model_key=model_key,
                image=original,
                detections=[detection],
                roi_regions=[region],
                localization_model=localization_model,
                validated_spec=student_spec,
            )
        except StudentVLMError as exc:
            result = {
                "status": "failed",
                "model_key": model_key,
                "model_name": student_spec.display_name,
                "model_id": student_spec.model_id,
                "error": str(exc),
                "input_mode": "single_selected_yolo_roi",
            }
        report = dict(result)
        report["region_index"] = int(region["region_index"])
        report["detection_index"] = detection_index
        report["region"] = next(
            item for item in serialized_regions
            if int(item.get("region_index", -1)) == int(region["region_index"])
        )
        return report

    max_workers = min(
        len(roi_regions),
        max(1, int(os.environ.get("PATHOVISION_VLM_MAX_CONCURRENT_PER_MODEL", "2"))),
    )
    ordered_reports: list[dict[str, Any] | None] = [None] * len(roi_regions)
    if len(roi_regions) == 1:
        ordered_reports[0] = analyze_one_region(roi_regions[0])
    else:
        with ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix=f"pathovision-{model_key}-roi",
        ) as executor:
            pending = {
                executor.submit(analyze_one_region, region): position
                for position, region in enumerate(roi_regions)
            }
            for future in as_completed(pending):
                ordered_reports[pending[future]] = future.result()
    region_reports = [report for report in ordered_reports if isinstance(report, dict)]
    completed_reports = [
        report for report in region_reports
        if report.get("status") == "completed"
        and isinstance(report.get("structured_output"), dict)
    ]
    if len(completed_reports) == len(region_reports):
        overall_status = "completed"
    elif completed_reports:
        overall_status = "partial"
    else:
        overall_status = "failed"
    summaries = [
        f"異常區域 {report['detection_index']}：{str(report.get('summary', '')).strip()}"
        for report in completed_reports
        if str(report.get("summary", "")).strip()
    ]
    student_result: dict[str, Any] = {
        "status": overall_status,
        "model_key": model_key,
        "model_name": student_spec.display_name,
        "model_id": student_spec.model_id,
        "parameter_scale": student_spec.parameter_scale,
        "summary": "\n".join(summaries),
        "input_mode": "independent_selected_yolo_rois",
        "input_image_count": len(roi_regions),
        "regions": serialized_regions,
        "region_reports": region_reports,
    }
    if completed_reports:
        # Retain the first successful output for older clients while the canonical
        # multi-ROI representation lives in region_reports.
        student_result["structured_output"] = completed_reports[0]["structured_output"]
        student_result["controls"] = completed_reports[0].get("controls", {})
    failures = [
        f"異常區域 {report.get('detection_index')}：{report.get('error', report.get('status'))}"
        for report in region_reports
        if report.get("status") != "completed"
    ]
    if failures:
        student_result["error"] = "；".join(failures)
    return update_case_student_analysis(metadata, original, student_result, roi_regions)


@app.post(
    "/api/v1/cases",
    response_model=AnalysisRecord,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_api_key)],
)
def create_manual_case(request: CaseCreateRequest) -> dict[str, Any]:
    """Create a backend-backed case record before an image analysis is available."""
    requested_id = request.case_id.strip()
    target_id = normalize_case_id(requested_id) if requested_id else ""
    with _case_mutation_lock:
        if target_id:
            target_dir = case_directory(target_id)
            if target_dir.exists() or target_dir.is_symlink():
                raise HTTPException(status_code=409, detail="The requested case ID already exists.")

        placeholder = Image.new("RGB", (960, 540), (244, 247, 252))
        draw = ImageDraw.Draw(placeholder)
        draw.rounded_rectangle((170, 145, 790, 395), radius=24, outline=(184, 195, 218), width=4)
        draw.text((330, 250), "Manual case - no image analysis", fill=(82, 96, 126))
        metadata = create_case(
            placeholder,
            placeholder.copy(),
            [],
            0.25,
            0.45,
            100,
            None,
            [],
            DEFAULT_LOCALIZATION_MODEL,
        )
        generated_id = str(metadata["case_id"])
        metadata.pop("artifacts", None)
        metadata["model"] = {
            "key": "manual",
            "name": "Manual record",
            "weights": "",
            "student_vlm": {"key": "", "name": "", "model_id": ""},
        }
        metadata["parameters"]["localization_model"] = ""
        metadata["parameters"]["record_source"] = "manual"
        metadata["analysis"].update(
            {
                "candidate_count": 0,
                "top_label": "未分析",
                "max_confidence": 0.0,
                "status": "未分析",
                "localization_assessment": "此個案由使用者手動建立，尚未執行影像分析。",
                "ai_assessment": "此個案由使用者手動建立，尚未執行影像分析。",
                "label_distribution": {},
                "student_vlm": {"status": "not_requested"},
                "vlm_regions": [],
            }
        )
        metadata["report"] = ReportUpdate().model_dump()
        atomic_write_json(case_directory(generated_id) / "analysis.json", metadata)
        if target_id and target_id != generated_id:
            return update_case_record_data(generated_id, target_id, ReportUpdate())
        return load_metadata(generated_id)


@app.patch(
    "/api/v1/analyses/{case_id}",
    response_model=AnalysisRecord,
    dependencies=[Depends(require_api_key)],
)
def update_case_record(case_id: str, update: CaseUpdateRequest) -> dict[str, Any]:
    return update_case_record_data(case_id, update.case_id, update.report)


@app.get(
    "/api/v1/analyses",
    response_model=list[AnalysisListItem],
    dependencies=[Depends(require_api_key)],
)
def list_analyses(
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[AnalysisListItem]:
    items: list[AnalysisListItem] = []
    for child in CASE_ROOT.iterdir():
        if child.is_symlink() or not child.is_dir() or not CASE_ID_PATTERN.fullmatch(child.name):
            continue
        try:
            items.append(analysis_list_item(load_metadata(child.name)))
        except HTTPException:
            continue
    items.sort(key=lambda item: (item.created_at, item.case_id), reverse=True)
    return items[offset : offset + limit]


@app.get(
    "/api/v1/analyses/{case_id}",
    response_model=AnalysisRecord,
    dependencies=[Depends(require_api_key)],
)
def get_analysis(case_id: str) -> dict[str, Any]:
    return load_metadata(case_id)


@app.get(
    "/api/v1/analyses/{case_id}/images/{kind}",
    dependencies=[Depends(require_api_key)],
    response_class=FileResponse,
)
def get_analysis_image(case_id: str, kind: Literal["original", "localized"]) -> FileResponse:
    metadata = load_metadata(case_id)
    file_name = Path(str(metadata.get("files", {}).get(kind, f"{kind}.png"))).name
    path = case_directory(case_id) / file_name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Image artifact not found.")
    return FileResponse(path, media_type="image/png", filename=f"{case_id}-{kind}.png")


@app.get(
    "/api/v1/analyses/{case_id}/images/regions/{region_index}",
    dependencies=[Depends(require_api_key)],
    response_class=FileResponse,
)
def get_analysis_region(case_id: str, region_index: int) -> FileResponse:
    metadata = load_metadata(case_id)
    region_files = metadata.get("files", {}).get("regions", [])
    if not isinstance(region_files, list) or not 1 <= region_index <= len(region_files):
        raise HTTPException(status_code=404, detail="ROI artifact not found.")
    file_name = str(region_files[region_index - 1])
    if Path(file_name).name != file_name:
        raise HTTPException(status_code=500, detail="Stored ROI artifact is invalid.")
    path = case_directory(case_id) / file_name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="ROI artifact not found.")
    return FileResponse(
        path,
        media_type="image/png",
        filename=f"{case_id}-roi-{region_index:03d}.png",
    )


@app.patch(
    "/api/v1/analyses/{case_id}/report",
    response_model=AnalysisRecord,
    dependencies=[Depends(require_api_key)],
)
def update_report(case_id: str, update: ReportUpdate) -> dict[str, Any]:
    metadata = load_metadata(case_id)
    metadata.pop("artifacts", None)
    metadata["report"] = update.model_dump()
    atomic_write_json(case_directory(case_id) / "analysis.json", metadata)
    return load_metadata(case_id)


def _same_json_shape(original: Any, edited: Any) -> bool:
    if isinstance(original, dict):
        return (
            isinstance(edited, dict)
            and original.keys() == edited.keys()
            and all(_same_json_shape(original[key], edited[key]) for key in original)
        )
    if isinstance(original, list):
        return (
            isinstance(edited, list)
            and len(original) == len(edited)
            and all(_same_json_shape(left, right) for left, right in zip(original, edited))
        )
    if isinstance(original, bool) or isinstance(edited, bool):
        return type(original) is type(edited)
    if isinstance(original, (int, float)):
        return isinstance(edited, (int, float))
    return type(original) is type(edited)


@app.patch(
    "/api/v1/analyses/{case_id}/structured-analysis",
    response_model=AnalysisRecord,
    dependencies=[Depends(require_api_key)],
)
def update_structured_analysis(
    case_id: str, update: StructuredAnalysisUpdate
) -> dict[str, Any]:
    """Persist one user-edited ROI report while preserving its schema shape."""
    with _case_mutation_lock:
        metadata = load_metadata(case_id)
        metadata.pop("artifacts", None)
        student_vlm = metadata.get("analysis", {}).get("student_vlm", {})
        reports = student_vlm.get("region_reports", [])
        target_report: dict[str, Any] | None = None
        if isinstance(reports, list) and reports:
            completed_reports = [
                report for report in reports
                if isinstance(report, dict)
                and report.get("status") == "completed"
                and isinstance(report.get("structured_output"), dict)
            ]
            if update.detection_index is None:
                target_report = completed_reports[0] if completed_reports else None
            else:
                target_report = next(
                    (
                        report for report in completed_reports
                        if report.get("detection_index") == update.detection_index
                    ),
                    None,
                )
            original = target_report.get("structured_output") if target_report else None
        else:
            original = student_vlm.get("structured_output")
        edited = update.structured_output
        if not isinstance(original, dict):
            raise HTTPException(
                status_code=409,
                detail="This case has no completed structured analysis for the selected region.",
            )
        if not _same_json_shape(original, edited):
            raise HTTPException(
                status_code=422,
                detail="Edited report must preserve every structured field and value type.",
            )
        if len(json.dumps(edited, ensure_ascii=False).encode("utf-8")) > 2 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Edited structured report is too large.")

        edited_at = datetime.now().astimezone().isoformat(timespec="seconds")
        case_dir = case_directory(case_id)
        if target_report is not None:
            target_report["structured_output"] = edited
            target_report["user_edited"] = True
            target_report["user_edited_at"] = edited_at
            region_name = _student_report_filename(target_report)
            if region_name:
                atomic_write_json(case_dir / region_name, edited)
                target_report["structured_output_file"] = region_name
            first_completed = next(
                (
                    report for report in reports
                    if isinstance(report, dict)
                    and report.get("status") == "completed"
                    and isinstance(report.get("structured_output"), dict)
                ),
                None,
            )
            if target_report is first_completed:
                student_vlm["structured_output"] = edited
                vlm_name = Path(str(metadata.get("files", {}).get("student_vlm", ""))).name
                if vlm_name:
                    atomic_write_json(case_dir / vlm_name, edited)
        else:
            student_vlm["structured_output"] = edited
            vlm_name = Path(str(metadata.get("files", {}).get("student_vlm", ""))).name
            if vlm_name:
                atomic_write_json(case_dir / vlm_name, edited)
        student_vlm["user_edited"] = True
        student_vlm["user_edited_at"] = edited_at
        atomic_write_json(case_dir / "analysis.json", metadata)
        return load_metadata(case_id)


@app.delete(
    "/api/v1/analyses/{case_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_api_key)],
)
def delete_analysis(case_id: str) -> None:
    with _case_mutation_lock:
        path = case_directory(case_id)
        if path.is_symlink() or not path.is_dir():
            raise HTTPException(status_code=404, detail="Case not found.")
        shutil.rmtree(path)
