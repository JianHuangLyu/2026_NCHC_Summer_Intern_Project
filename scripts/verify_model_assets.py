#!/usr/bin/env python3
"""Validate PathoVision model locations without loading model weights."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


MODELS = {
    "gemma4": {
        "folder": Path("Student_model/Gemma4"),
        "weights": "gemma-4-31B-it",
        "required_weight_files": (
            "config.json",
            "model.safetensors.index.json",
            "processor_config.json",
            "tokenizer_config.json",
        ),
    },
    "mistral-small-3.1": {
        "folder": Path("Student_model/Mistral-Small-3.1"),
        "weights": "mistral-small-3.1-24b-instruct-2503",
        "required_weight_files": (
            "config.json",
            "consolidated.safetensors",
            "params.json",
            "preprocessor_config.json",
            "tokenizer_config.json",
        ),
    },
}

CONTROL_FILES = (
    "Prompt.md",
    "Global_Rules.md",
    "Output_Schema.json",
    "Output_Field_Skill_Mapping.yaml",
    "Skill_Registry.yaml",
)

YOLO_HASHES = {
    "yolo11s_best.pt": "cf4e5586549d2996a1caef20eaef15b4b2c30884c5a8493a465be4f600a6251b",
    "yolo11m_best.pt": "349190105b061288c600eccc64ecb6276967af0191b7bd21b96147a821341c5b",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_model(project_root: Path, key: str) -> list[str]:
    spec = MODELS[key]
    base = project_root / spec["folder"]
    weights = base / str(spec["weights"])
    prompt = base / "best_prompt"
    skills = base / "best_skills"
    errors: list[str] = []

    for relative in spec["required_weight_files"]:
        path = weights / relative
        if not path.is_file() or path.stat().st_size == 0:
            errors.append(f"missing or empty: {path}")

    weight_files = sorted(weights.glob("*.safetensors")) if weights.is_dir() else []
    if not weight_files:
        errors.append(f"no .safetensors weights found: {weights}")
    elif any(path.stat().st_size == 0 for path in weight_files):
        errors.append(f"one or more empty .safetensors files: {weights}")

    index_path = weights / "model.safetensors.index.json"
    if index_path.is_file():
        try:
            payload = json.loads(index_path.read_text(encoding="utf-8"))
            shards = set(payload.get("weight_map", {}).values())
            for shard in sorted(shards):
                if not (weights / shard).is_file():
                    errors.append(f"index references missing shard: {weights / shard}")
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"invalid index JSON: {index_path}: {exc}")

    for filename in CONTROL_FILES:
        path = prompt / filename
        if not path.is_file() or path.stat().st_size == 0:
            errors.append(f"missing or empty control file: {path}")
    if not any(skills.glob("*.md")):
        errors.append(f"no Skill Markdown files found: {skills}")

    return errors


def validate_yolo(project_root: Path) -> list[str]:
    errors: list[str] = []
    folder = project_root / "Localization_model"
    for filename, expected in YOLO_HASHES.items():
        path = folder / filename
        if not path.is_file():
            errors.append(f"missing YOLO weight: {path}")
            continue
        actual = sha256(path)
        if actual != expected:
            errors.append(f"SHA-256 mismatch: {path} (got {actual})")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument(
        "--model",
        choices=("all", *MODELS),
        default="all",
    )
    parser.add_argument(
        "--include-yolo",
        action="store_true",
        help="also verify the separately distributed YOLO weights",
    )
    args = parser.parse_args()

    project_root = args.project_root.expanduser().resolve()
    selected = MODELS if args.model == "all" else {args.model: MODELS[args.model]}
    errors: list[str] = []
    for key in selected:
        model_errors = validate_model(project_root, key)
        if model_errors:
            print(f"[FAIL] {key}")
            errors.extend(model_errors)
        else:
            print(f"[ OK ] {key}")
    if args.include_yolo:
        yolo_errors = validate_yolo(project_root)
        if yolo_errors:
            print("[FAIL] YOLO")
            errors.extend(yolo_errors)
        else:
            print("[ OK ] YOLO")

    if errors:
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print("All selected model assets are present.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
