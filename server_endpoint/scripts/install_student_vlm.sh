#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
INSTALLER_VENV="${PATHOVISION_HF_INSTALLER_VENV:-$PROJECT_DIR/.hf-model-installer}"
MODEL_SELECTION="all"
VERIFY_ONLY=0

usage() {
  cat <<'EOF'
Usage: scripts/install_student_vlm.sh [options]

Download Student VLM snapshots to the exact directories expected by PathoVision.

Options:
  --model all|gemma4|mistral-small-3.1  Models to install (default: all)
  --verify-only                         Validate files without downloading
  -h, --help                            Show this help

Authentication:
  Run hf auth login first, or export HF_TOKEN. Never put a token in Git.
EOF
}

while (($#)); do
  case "$1" in
    --model)
      if (($# < 2)); then
        echo "--model requires a value" >&2
        exit 2
      fi
      MODEL_SELECTION="$2"
      shift 2
      ;;
    --verify-only)
      VERIFY_ONLY=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

case "$MODEL_SELECTION" in
  all|gemma4|mistral-small-3.1) ;;
  *)
    echo "Invalid model: $MODEL_SELECTION" >&2
    exit 2
    ;;
esac

if ((VERIFY_ONLY)); then
  exec "$PYTHON_BIN" "$SCRIPT_DIR/verify_model_assets.py" \
    --project-root "$PROJECT_DIR" --model "$MODEL_SELECTION"
fi

command -v "$PYTHON_BIN" >/dev/null 2>&1 || {
  echo "Python executable not found: $PYTHON_BIN" >&2
  exit 1
}

echo "Project root: $PROJECT_DIR"
echo "Installer environment: $INSTALLER_VENV"
echo "Selected model(s): $MODEL_SELECTION"
df -h "$PROJECT_DIR" | tail -n 1

if [ ! -x "$INSTALLER_VENV/bin/python" ]; then
  "$PYTHON_BIN" -m venv "$INSTALLER_VENV"
fi
"$INSTALLER_VENV/bin/python" -m pip install --upgrade pip huggingface_hub

"$INSTALLER_VENV/bin/python" - "$PROJECT_DIR" "$MODEL_SELECTION" <<'PY'
from __future__ import annotations

import os
import sys
from pathlib import Path

from huggingface_hub import snapshot_download

project_root = Path(sys.argv[1]).resolve()
selection = sys.argv[2]
models = {
    "gemma4": (
        "google/gemma-4-31B-it",
        project_root / "Student_model" / "Gemma4" / "gemma-4-31B-it",
        os.environ.get("PATHOVISION_GEMMA_REVISION", "main"),
    ),
    "mistral-small-3.1": (
        "mistralai/Mistral-Small-3.1-24B-Instruct-2503",
        project_root
        / "Student_model"
        / "Mistral-Small-3.1"
        / "mistral-small-3.1-24b-instruct-2503",
        os.environ.get("PATHOVISION_MISTRAL_REVISION", "main"),
    ),
}
selected = models if selection == "all" else {selection: models[selection]}
token = os.environ.get("HF_TOKEN") or None

for key, (repo_id, destination, revision) in selected.items():
    destination.mkdir(parents=True, exist_ok=True)
    print(f"\n[{key}] Downloading {repo_id} at revision {revision}")
    print(f"[{key}] Destination: {destination}")
    snapshot_download(
        repo_id=repo_id,
        local_dir=destination,
        revision=revision,
        token=token,
    )
PY

"$INSTALLER_VENV/bin/python" "$SCRIPT_DIR/verify_model_assets.py" \
  --project-root "$PROJECT_DIR" --model "$MODEL_SELECTION"

echo "Student VLM installation completed."
