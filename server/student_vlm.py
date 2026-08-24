"""Student VLM discovery and OpenAI-compatible pathology inference.

The model weights and their optimized text controls are treated as read-only.
This module does not load the large VLM weights in the FastAPI process.  Each
model is served by a dedicated OpenAI-compatible endpoint (normally vLLM), so
the API can keep YOLO localization responsive and switch models per request.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from PIL import Image


@dataclass(frozen=True)
class StudentModelSpec:
    key: str
    display_name: str
    model_id: str
    folder: str
    model_folder: str
    parameter_scale: str
    endpoint_env: str
    api_key_env: str


MODEL_SPECS = (
    StudentModelSpec(
        key="gemma4",
        display_name="Gemma 4 31B",
        model_id="google/gemma-4-31B-it",
        folder="Gemma4",
        model_folder="gemma-4-31B-it",
        parameter_scale="31B",
        endpoint_env="PATHOVISION_GEMMA4_BASE_URL",
        api_key_env="PATHOVISION_GEMMA4_API_KEY",
    ),
    StudentModelSpec(
        key="mistral-small-3.1",
        display_name="Mistral Small 3.1 24B",
        model_id="mistralai/Mistral-Small-3.1-24B-Instruct-2503",
        folder="Mistral-Small-3.1",
        model_folder="mistral-small-3.1-24b-instruct-2503",
        parameter_scale="24B",
        endpoint_env="PATHOVISION_MISTRAL_BASE_URL",
        api_key_env="PATHOVISION_MISTRAL_API_KEY",
    ),
    StudentModelSpec(
        key="phi-3.5-vision",
        display_name="Phi-3.5 Vision 4.2B",
        model_id="microsoft/Phi-3.5-vision-instruct",
        folder="Phi-3.5-Vision",
        model_folder="phi-3.5-vision-instruct",
        parameter_scale="4.2B",
        endpoint_env="PATHOVISION_PHI35_BASE_URL",
        api_key_env="PATHOVISION_PHI35_API_KEY",
    ),
)

SPEC_BY_KEY = {spec.key: spec for spec in MODEL_SPECS}
_MAX_CONCURRENT_PER_MODEL = max(
    1, int(os.environ.get("PATHOVISION_VLM_MAX_CONCURRENT_PER_MODEL", "2"))
)
# vLLM can batch independent requests on one GPU. Keep a bounded number in flight
# instead of serializing the entire HTTP request path with a single lock.
_inference_slots = {
    spec.key: threading.BoundedSemaphore(_MAX_CONCURRENT_PER_MODEL)
    for spec in MODEL_SPECS
}


class StudentVLMError(RuntimeError):
    """Base error returned to the API integration layer."""


class UnknownStudentModelError(StudentVLMError):
    pass


class StudentAssetsNotReadyError(StudentVLMError):
    pass


class StudentEndpointNotReadyError(StudentVLMError):
    pass


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _model_paths(root: Path, spec: StudentModelSpec) -> dict[str, Path]:
    model_root = root / spec.folder
    return {
        "root": model_root,
        "weights": model_root / spec.model_folder,
        "prompt": model_root / "best_prompt",
        "skills": model_root / "best_skills",
    }


def _endpoint_is_ready(endpoint: str, expected_model_id: str) -> bool:
    """Return True only after the local OpenAI-compatible VLM is responding."""
    if not endpoint:
        return False
    timeout = float(os.environ.get("PATHOVISION_VLM_PROBE_TIMEOUT_SECONDS", "0.5"))
    request = urllib.request.Request(
        endpoint.rstrip("/") + "/models",
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=max(0.1, timeout)) as response:
            if response.status != 200:
                return False
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
        return False
    models = payload.get("data", []) if isinstance(payload, dict) else []
    model_ids = {
        str(item.get("id", ""))
        for item in models
        if isinstance(item, dict) and item.get("id")
    }
    return expected_model_id in model_ids


def _registry_skill_files(registry_path: Path) -> list[str]:
    try:
        registry = yaml.safe_load(_read_text(registry_path)) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise StudentAssetsNotReadyError(
            f"Unreadable Skill registry: {registry_path.name}: {exc}"
        ) from exc

    files: list[str] = []
    for value in registry.get("required", []):
        if isinstance(value, str) and value.endswith(".md"):
            files.append(value)
    for value in registry.get("conditional", []):
        if isinstance(value, dict):
            filename = value.get("file")
            if isinstance(filename, str) and filename.endswith(".md"):
                files.append(filename)
    integration = registry.get("integration_last")
    if isinstance(integration, dict):
        filename = integration.get("file")
        if isinstance(filename, str) and filename.endswith(".md"):
            files.append(filename)
    return list(dict.fromkeys(files))


def inspect_student_model(root: Path, spec: StudentModelSpec) -> dict[str, Any]:
    paths = _model_paths(root, spec)
    prompt_dir = paths["prompt"]
    skill_dir = paths["skills"]
    required_prompt_files = (
        "Prompt.md",
        "Global_Rules.md",
        "Skill_Registry.yaml",
        "Output_Schema.json",
        "Output_Field_Skill_Mapping.yaml",
    )
    missing: list[str] = []

    if not (paths["weights"] / "config.json").is_file():
        missing.append(f"{spec.model_folder}/config.json")
    if not any(paths["weights"].glob("*.safetensors")):
        missing.append(f"{spec.model_folder}/*.safetensors")
    for filename in required_prompt_files:
        if not (prompt_dir / filename).is_file():
            missing.append(f"best_prompt/{filename}")

    registry_skills: list[str] = []
    registry_path = prompt_dir / "Skill_Registry.yaml"
    if registry_path.is_file():
        try:
            registry_skills = _registry_skill_files(registry_path)
        except StudentAssetsNotReadyError as exc:
            missing.append(str(exc))
    for filename in registry_skills:
        if not (skill_dir / filename).is_file():
            missing.append(f"best_skills/{filename}")

    endpoint = os.environ.get(spec.endpoint_env, "").strip()
    endpoint_ready = _endpoint_is_ready(endpoint, spec.model_id)
    assets_ready = not missing and bool(registry_skills)
    return {
        "key": spec.key,
        "display_name": spec.display_name,
        "model_id": spec.model_id,
        "parameter_scale": spec.parameter_scale,
        "assets_ready": assets_ready,
        "endpoint_configured": bool(endpoint),
        "endpoint_ready": endpoint_ready,
        "inference_ready": assets_ready and endpoint_ready,
        "skill_count": len(list(skill_dir.glob("*.md"))) if skill_dir.is_dir() else 0,
        "registry_skill_count": len(registry_skills),
        "missing_assets": missing,
    }


def list_student_models(root: Path) -> list[dict[str, Any]]:
    return [inspect_student_model(root, spec) for spec in MODEL_SPECS]


def require_student_model(root: Path, model_key: str) -> tuple[StudentModelSpec, dict[str, Any]]:
    spec = SPEC_BY_KEY.get(model_key)
    if spec is None:
        choices = ", ".join(SPEC_BY_KEY)
        raise UnknownStudentModelError(
            f"Unknown analysis-inference model {model_key!r}; choose one of: {choices}."
        )
    status = inspect_student_model(root, spec)
    if not status["assets_ready"]:
        raise StudentAssetsNotReadyError(
            f"{spec.display_name} assets are incomplete: "
            + ", ".join(status["missing_assets"])
        )
    if not status["endpoint_configured"]:
        raise StudentEndpointNotReadyError(
            f"{spec.display_name} endpoint is not configured; set {spec.endpoint_env}."
        )
    if not status["endpoint_ready"]:
        raise StudentEndpointNotReadyError(
            f"{spec.display_name} is still loading; retry after its vLLM endpoint is ready."
        )
    return spec, status


@lru_cache(maxsize=len(MODEL_SPECS) * 2)
def compose_system_prompt(root: Path, spec: StudentModelSpec) -> tuple[str, dict[str, Any]]:
    """Load and cache one model bundle's immutable best prompt and Skills."""
    paths = _model_paths(root, spec)
    prompt_dir = paths["prompt"]
    skill_dir = paths["skills"]
    registry_path = prompt_dir / "Skill_Registry.yaml"
    skill_files = _registry_skill_files(registry_path)
    schema = json.loads(_read_text(prompt_dir / "Output_Schema.json"))

    sections = [
        "# Pathology Image Auxiliary Analysis Controls",
        "## Main Prompt\n" + _read_text(prompt_dir / "Prompt.md"),
        "## Global Rules\n" + _read_text(prompt_dir / "Global_Rules.md"),
        "## Skill Registry\n```yaml\n"
        + _read_text(registry_path)
        + "\n```",
        "## Output JSON Schema\n```json\n"
        + json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
        + "\n```",
        "## Output Field-to-Skill Mapping\n```yaml\n"
        + _read_text(prompt_dir / "Output_Field_Skill_Mapping.yaml")
        + "\n```",
        (
            "## Runtime Loader State\n"
            f"The controls in this request were loaded from the selected {spec.display_name} "
            "bundle. Every Skill named by Skill_Registry.yaml is available below. Required "
            "Skills are loaded in registry order; conditional Skills must be activated only "
            "when their registry condition is supported by visible evidence. Paths written "
            "inside the original Prompt.md are provenance labels; do not attempt filesystem "
            "access because the resolved file contents are embedded in this system message."
        ),
        "# Available Pathology Skills",
    ]
    for filename in skill_files:
        sections.append(f"## Skill File: {filename}\n{_read_text(skill_dir / filename)}")
    sections.append(
        "# Final Runtime Requirement\n"
        "Use only the controls and Skills above. Return exactly one JSON object "
        "conforming to the supplied Output JSON Schema. Do not wrap it in Markdown "
        "and do not add commentary before or after it."
    )
    return "\n\n".join(sections), schema


def _prepare_vlm_image(image: Image.Image) -> Image.Image:
    """Bound visual tokens and request size while preserving ROI aspect ratio."""
    max_edge = max(224, int(os.environ.get("PATHOVISION_VLM_MAX_IMAGE_EDGE", "1280")))
    max_pixels = max(
        224 * 224,
        int(os.environ.get("PATHOVISION_VLM_MAX_IMAGE_PIXELS", "1600000")),
    )
    prepared = image.convert("RGB")
    width, height = prepared.size
    scale = min(
        1.0,
        max_edge / max(width, height),
        (max_pixels / max(1, width * height)) ** 0.5,
    )
    if scale < 1.0:
        prepared = prepared.resize(
            (max(1, round(width * scale)), max(1, round(height * scale))),
            Image.Resampling.LANCZOS,
        )
    return prepared


def _image_data_uri(image: Image.Image) -> str:
    buffer = BytesIO()
    _prepare_vlm_image(image).save(buffer, format="PNG", compress_level=1)
    mime = mimetypes.guess_type("image.png")[0] or "image/png"
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def prepare_roi_regions(
    image: Image.Image,
    detections: list[dict[str, Any]],
    *,
    max_regions: int | None = None,
    padding_ratio: float | None = None,
) -> list[dict[str, Any]]:
    """Crop the strongest valid YOLO regions for direct VLM inspection."""
    limit = max_regions if max_regions is not None else int(
        os.environ.get("PATHOVISION_MAX_VLM_ROIS", "4")
    )
    padding = padding_ratio if padding_ratio is not None else float(
        os.environ.get("PATHOVISION_VLM_ROI_PADDING_RATIO", "0.08")
    )
    limit = max(0, limit)
    padding = max(0.0, min(padding, 0.5))
    width, height = image.size
    rgb_image = image.convert("RGB")
    regions: list[dict[str, Any]] = []
    if limit == 0:
        return regions

    def confidence_value(item: dict[str, Any]) -> float:
        try:
            return float(item.get("confidence", 0.0))
        except (TypeError, ValueError):
            return 0.0

    ranked = sorted(detections, key=confidence_value, reverse=True)
    for detection in ranked:
        raw_box = detection.get("bbox_xyxy")
        if not isinstance(raw_box, (list, tuple)) or len(raw_box) != 4:
            continue
        try:
            x1, y1, x2, y2 = (float(value) for value in raw_box)
        except (TypeError, ValueError):
            continue
        x1, x2 = sorted((x1, x2))
        y1, y2 = sorted((y1, y2))
        x1, x2 = max(0.0, x1), min(float(width), x2)
        y1, y2 = max(0.0, y1), min(float(height), y2)
        if x2 - x1 < 1 or y2 - y1 < 1:
            continue
        pad_x = (x2 - x1) * padding
        pad_y = (y2 - y1) * padding
        crop_box = [
            max(0, int(x1 - pad_x)),
            max(0, int(y1 - pad_y)),
            min(width, int(x2 + pad_x + 0.999999)),
            min(height, int(y2 + pad_y + 0.999999)),
        ]
        crop = rgb_image.crop(tuple(crop_box))
        regions.append(
            {
                "region_index": len(regions) + 1,
                "detection_index": detection.get("index"),
                "label": detection.get("class_name", ""),
                "confidence": float(detection.get("confidence", 0.0)),
                "bbox_xyxy": [round(value, 2) for value in (x1, y1, x2, y2)],
                "crop_bbox_xyxy": crop_box,
                "width": crop.width,
                "height": crop.height,
                "image": crop,
            }
        )
        if len(regions) >= limit:
            break
    return regions


def serialize_roi_regions(regions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return JSON-safe ROI metadata without embedded PIL images."""
    return [
        {key: value for key, value in region.items() if key != "image"}
        for region in regions
    ]


def _completion_url(base_url: str) -> str:
    value = base_url.rstrip("/")
    if value.endswith("/chat/completions"):
        return value
    return value + "/chat/completions"


def _http_completion(
    *,
    endpoint: str,
    api_key: str,
    payload: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    request = urllib.request.Request(
        _completion_url(endpoint),
        data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key or 'dummy'}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:2000]
        raise StudentVLMError(f"Student endpoint returned HTTP {exc.code}: {body}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise StudentVLMError(f"Student endpoint request failed: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise StudentVLMError("Student endpoint returned invalid JSON.") from exc


def _extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.removeprefix("```json").removeprefix("```")
        if stripped.endswith("```"):
            stripped = stripped[:-3]
        stripped = stripped.strip()
    decoder = json.JSONDecoder()
    for index, character in enumerate(stripped):
        if character != "{":
            continue
        try:
            value, _end = decoder.raw_decode(stripped[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise StudentVLMError("Student output does not contain a valid JSON object.")


def _validate_prediction(prediction: dict[str, Any], schema: dict[str, Any]) -> None:
    errors = sorted(Draft202012Validator(schema).iter_errors(prediction), key=lambda item: list(item.path))
    if not errors:
        return
    messages = []
    for error in errors[:10]:
        path = ".".join(str(value) for value in error.path) or "<root>"
        messages.append(f"{path}: {error.message}")
    raise StudentVLMError("Student output violates Output_Schema.json: " + "; ".join(messages))


def _guided_decoding_schema(value: Any) -> Any:
    """Remove unsupported hints from guided decoding; validate them after generation."""
    if isinstance(value, dict):
        return {
            key: _guided_decoding_schema(item)
            for key, item in value.items()
            if key != "uniqueItems"
        }
    if isinstance(value, list):
        return [_guided_decoding_schema(item) for item in value]
    return value


def analyze_with_student(
    *,
    root: Path,
    model_key: str,
    image: Image.Image,
    detections: list[dict[str, Any]],
    roi_regions: list[dict[str, Any]] | None = None,
    localization_model: str = "yolo11m",
    validated_spec: StudentModelSpec | None = None,
) -> dict[str, Any]:
    if validated_spec is not None and validated_spec.key != model_key:
        raise StudentVLMError("Validated Student model does not match the requested model.")
    spec = validated_spec
    if spec is None:
        spec, _status = require_student_model(root, model_key)
    system_prompt, schema = compose_system_prompt(root, spec)
    endpoint = os.environ[spec.endpoint_env].strip()
    api_key = os.environ.get(spec.api_key_env, "dummy").strip() or "dummy"
    max_tokens = max(512, int(os.environ.get("PATHOVISION_VLM_MAX_TOKENS", "8192")))
    timeout = float(os.environ.get("PATHOVISION_VLM_TIMEOUT_SECONDS", "600"))
    regions = roi_regions if roi_regions is not None else prepare_roi_regions(image, detections)
    if not regions:
        raise StudentVLMError("No selected YOLO regions were supplied for Student analysis.")
    region_metadata = serialize_roi_regions(regions)
    # The structured stage is deliberately ROI-only.  The original image remains
    # stored with the case but is never sent to the VLM.
    width, height = image.size
    visible_regions = [
        {
            "index": item.get("index"),
            "label": item.get("class_name"),
            "confidence": item.get("confidence"),
            "bbox_xyxy": item.get("bbox_xyxy"),
        }
        for item in detections
    ]
    context = {
        "image_type": "ROI",
        "stain": "Unknown",
        "image_dimensions": {"width": width, "height": height},
        "localization_context": {
            "source": localization_model,
            "candidate_regions": visible_regions,
            "vlm_roi_regions": region_metadata,
            "instruction": (
                "Only the user-selected candidate regions are supplied for analysis. "
                "Candidate boxes are localization hints only. Verify all morphology "
                "directly from the unannotated ROI images and do not treat class labels "
                "as diagnosis. Do not extrapolate beyond the selected ROIs."
            ),
        },
    }
    image_manifest: list[dict[str, Any]] = []
    multimodal_content: list[dict[str, Any]] = []
    for region in regions:
        image_manifest.append(
            {
                "input_image_index": len(image_manifest) + 1,
                "kind": "user_selected_yolo_roi",
                "region_index": region.get("region_index"),
                "detection_index": region.get("detection_index"),
                "label_hint": region.get("label"),
                "confidence": region.get("confidence"),
                "bbox_xyxy": region.get("bbox_xyxy"),
                "crop_bbox_xyxy": region.get("crop_bbox_xyxy"),
            }
        )
        multimodal_content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": _image_data_uri(region["image"]),
                    "detail": "high",
                },
            }
        )
    context["input_image_manifest"] = image_manifest
    user_text = (
        "Analyze only the directly visible morphology in the user-selected pathology ROIs. "
        "Do not infer unseen clinical facts or diagnoses. Integrate the supplied YOLO "
        "candidate coordinates only as localization context. The preceding images are "
        "ordered exactly as input_image_manifest. Return exactly one JSON object "
        "conforming to Output_Schema.json, without Markdown. Preserve every JSON property "
        "name, schema-constrained enum/const value, Skill identifier, and control identifier "
        "exactly as defined. For all unconstrained human-readable narrative strings—including "
        "morphology Value descriptions, Supporting_Visible_Evidence, summaries, limitations, "
        "quality descriptions, and reasons—write in professional Traditional Chinese used in "
        "Taiwan (zh-Hant-TW), using standard pathology and medical terminology.\n\n"
        "Image context:\n"
        + json.dumps(context, ensure_ascii=False, separators=(",", ":"))
    )
    # Gemma 4 recommends image content before text; Mistral accepts the same order.
    multimodal_content.append({"type": "text", "text": user_text})

    payload: dict[str, Any] = {
        "model": spec.model_id,
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": multimodal_content,
            },
        ],
        "temperature": 0,
        "max_tokens": max_tokens,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "pathology_image_analysis",
                "schema": _guided_decoding_schema(schema),
            },
        },
    }

    with _inference_slots[spec.key]:
        try:
            response = _http_completion(
                endpoint=endpoint,
                api_key=api_key,
                payload=payload,
                timeout=timeout,
            )
        except StudentVLMError as exc:
            # Older OpenAI-compatible servers may not support response_format.
            error_text = str(exc).lower()
            if "http 400" not in error_text or not any(
                marker in error_text
                for marker in (
                    "response_format",
                    "json_schema",
                    "guided",
                    "grammar error",
                    "unimplemented keys",
                )
            ):
                raise
            payload.pop("response_format", None)
            response = _http_completion(
                endpoint=endpoint,
                api_key=api_key,
                payload=payload,
                timeout=timeout,
            )

    try:
        raw_text = response["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as exc:
        raise StudentVLMError("Student endpoint response has no assistant content.") from exc
    prediction = raw_text if isinstance(raw_text, dict) else _extract_json_object(str(raw_text))
    _validate_prediction(prediction, schema)
    summary = str(
        prediction.get("Morphological_Summary", {}).get("Direct_Observations_Only", "")
    ).strip()
    return {
        "status": "completed",
        "model_key": spec.key,
        "model_name": spec.display_name,
        "model_id": spec.model_id,
        "parameter_scale": spec.parameter_scale,
        "localization_model": localization_model,
        "summary": summary,
        "structured_output": prediction,
        "input_mode": "selected_yolo_rois",
        "input_image_count": len(regions),
        "regions": region_metadata,
        "controls": {
            "prompt": "best_prompt/Prompt.md",
            "global_rules": "best_prompt/Global_Rules.md",
            "schema": "best_prompt/Output_Schema.json",
            "field_skill_mapping": "best_prompt/Output_Field_Skill_Mapping.yaml",
            "narrative_language": "zh-Hant-TW",
            "skills": _registry_skill_files(
                _model_paths(root, spec)["prompt"] / "Skill_Registry.yaml"
            ),
        },
        "usage": response.get("usage", {}),
    }
