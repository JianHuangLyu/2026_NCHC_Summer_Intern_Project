#!/usr/bin/env bash
#SBATCH --job-name=pathovision-vlm-ui
#SBATCH --partition=dev
#SBATCH --time=04:00:00
#SBATCH --cpus-per-task=32
#SBATCH --mem=256G
#SBATCH --gres=gpu:3
#SBATCH --output=.pathovision_runtime/vlm-stack-%j.out
#SBATCH --error=.pathovision_runtime/vlm-stack-%j.err

set -euo pipefail
umask 077

PROJECT_DIR="${PATHOVISION_PROJECT_DIR:-${SLURM_SUBMIT_DIR:-$PWD}}"
RUNTIME_DIR="$PROJECT_DIR/.pathovision_runtime"
STUDENT_ROOT="${PATHOVISION_STUDENT_MODEL_ROOT:-$PROJECT_DIR/Student_model}"
SERVER_PYTHON="${PATHOVISION_SERVER_PYTHON:-$PROJECT_DIR/.venv/bin/python}"
VLLM_BIN="${PATHOVISION_VLLM_BIN:?Set PATHOVISION_VLLM_BIN to the vLLM executable}"
VLLM_BIN_DIR="$(dirname "$VLLM_BIN")"
VLLM_MAX_MODEL_LEN="${PATHOVISION_VLLM_MAX_MODEL_LEN:-49152}"
VLLM_SAFETENSORS_LOAD_STRATEGY="${PATHOVISION_SAFETENSORS_LOAD_STRATEGY:-prefetch}"
VLLM_SAFETENSORS_PREFETCH_THREADS="${PATHOVISION_SAFETENSORS_PREFETCH_THREADS:-8}"
VLLM_ENFORCE_EAGER="${PATHOVISION_VLLM_ENFORCE_EAGER:-0}"
VLLM_PERFORMANCE_MODE="${PATHOVISION_VLLM_PERFORMANCE_MODE:-interactivity}"
VLLM_MAX_NUM_SEQS="${PATHOVISION_VLLM_MAX_NUM_SEQS:-2}"
VLLM_SKIP_MM_PROFILING="${PATHOVISION_VLLM_SKIP_MM_PROFILING:-1}"

test -x "$SERVER_PYTHON"
test -x "$VLLM_BIN"
test -x "$VLLM_BIN_DIR/ninja"
case "$VLLM_PERFORMANCE_MODE" in
  balanced|interactivity|throughput) ;;
  *) echo "Invalid PATHOVISION_VLLM_PERFORMANCE_MODE: $VLLM_PERFORMANCE_MODE" >&2; exit 2 ;;
esac
if ! [[ "$VLLM_MAX_NUM_SEQS" =~ ^[1-9][0-9]*$ ]]; then
  echo "PATHOVISION_VLLM_MAX_NUM_SEQS must be a positive integer" >&2
  exit 2
fi
export PATH="$VLLM_BIN_DIR:$PATH"
export HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}"
export TRANSFORMERS_OFFLINE="${TRANSFORMERS_OFFLINE:-1}"
mkdir -p "$RUNTIME_DIR"
chmod 700 "$RUNTIME_DIR"
cd "$PROJECT_DIR"

mapfile -t PORTS < <("$SERVER_PYTHON" - <<'PY'
import socket
sockets = []
try:
    for _ in range(3):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("0.0.0.0", 0))
        sockets.append(sock)
    for sock in sockets:
        print(sock.getsockname()[1])
finally:
    for sock in sockets:
        sock.close()
PY
)

test "${#PORTS[@]}" -eq 3
GEMMA_PORT="${PORTS[0]}"
MISTRAL_PORT="${PORTS[1]}"
API_PORT="${PORTS[2]}"
NODE="${SLURMD_NODENAME:-$(hostname -s)}"
JOB_ID="${SLURM_JOB_ID:?SLURM_JOB_ID is required}"
ENV_FILE="$RUNTIME_DIR/${JOB_ID}.env"
if [ -z "${PATHOVISION_API_KEY:-}" ]; then
  PATHOVISION_API_KEY="$($SERVER_PYTHON - <<'PY'
import secrets
print(secrets.token_urlsafe(36))
PY
)"
fi
API_KEY="$PATHOVISION_API_KEY"

pids=()
LAST_PID=""
cleanup() {
  for pid in "${pids[@]:-}"; do
    kill "$pid" 2>/dev/null || true
  done
}
trap cleanup EXIT TERM INT

write_state() {
  local ready="$1"
  local stage="$2"
  local temporary="${ENV_FILE}.tmp"
  {
    printf 'JOB_ID=%s\n' "$JOB_ID"
    printf 'NODE=%s\n' "$NODE"
    printf 'PORT=%s\n' "$API_PORT"
    printf 'READY=%s\n' "$ready"
    printf 'STAGE=%s\n' "$stage"
    printf 'PROJECT_DIR=%s\n' "$PROJECT_DIR"
    printf 'API_KEY=%s\n' "$API_KEY"
  } >"$temporary"
  chmod 600 "$temporary"
  mv "$temporary" "$ENV_FILE"
}

start_vllm() {
  local gpu="$1"
  local port="$2"
  local model_path="$3"
  local served_name="$4"
  local log_name="$5"
  shift 5
  local -a eager_args=()
  local -a multimodal_startup_args=()
  if [ "$VLLM_ENFORCE_EAGER" = "1" ]; then
    eager_args+=(--enforce-eager)
  fi
  if [ "$VLLM_SKIP_MM_PROFILING" = "1" ]; then
    multimodal_startup_args+=(--skip-mm-profiling)
  fi
  test -f "$model_path/config.json"
  CUDA_VISIBLE_DEVICES="$gpu" "$VLLM_BIN" serve "$model_path" \
    --served-model-name "$served_name" \
    --host 127.0.0.1 \
    --port "$port" \
    --tensor-parallel-size 1 \
    --dtype auto \
    --max-model-len "$VLLM_MAX_MODEL_LEN" \
    --gpu-memory-utilization 0.88 \
    --max-num-seqs "$VLLM_MAX_NUM_SEQS" \
    --performance-mode "$VLLM_PERFORMANCE_MODE" \
    --mm-processor-cache-gb 0 \
    --enable-prefix-caching \
    --safetensors-load-strategy "$VLLM_SAFETENSORS_LOAD_STRATEGY" \
    --safetensors-prefetch-num-threads "$VLLM_SAFETENSORS_PREFETCH_THREADS" \
    "${multimodal_startup_args[@]}" \
    "${eager_args[@]}" \
    "$@" >"$RUNTIME_DIR/${log_name}-${JOB_ID}.log" 2>&1 &
  pids+=("$!")
  LAST_PID="$!"
}

wait_for_model() {
  local port="$1"
  local pid="$2"
  local name="$3"
  for _ in $(seq 1 900); do
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "$name service exited during startup" >&2
      return 1
    fi
    if "$SERVER_PYTHON" - "$port" <<'PY'
import sys
import urllib.request
try:
    with urllib.request.urlopen(
        f"http://127.0.0.1:{int(sys.argv[1])}/v1/models", timeout=1.5
    ) as response:
        raise SystemExit(0 if response.status == 200 else 1)
except Exception:
    raise SystemExit(1)
PY
    then
      echo "$name ready on port $port"
      return 0
    fi
    sleep 2
  done
  echo "$name service startup timed out" >&2
  return 1
}

export PATHOVISION_API_KEY="$API_KEY"
export PATHOVISION_STUDENT_MODEL_ROOT="$STUDENT_ROOT"
export PATHOVISION_DEFAULT_STUDENT_MODEL=""
export PATHOVISION_GEMMA4_BASE_URL="http://127.0.0.1:${GEMMA_PORT}/v1"
export PATHOVISION_MISTRAL_BASE_URL="http://127.0.0.1:${MISTRAL_PORT}/v1"
export PATHOVISION_MODEL_PATH="${PATHOVISION_MODEL_PATH:-$PROJECT_DIR/Localization_model/yolo11m_best.pt}"
export PATHOVISION_CASE_ROOT="${PATHOVISION_CASE_ROOT:-$PROJECT_DIR/.pathovision_server/cases}"
export PATHOVISION_DEVICE=0
export PATHOVISION_MAX_VLM_ROIS="${PATHOVISION_MAX_VLM_ROIS:-4}"
export PATHOVISION_VLM_ROI_PADDING_RATIO="${PATHOVISION_VLM_ROI_PADDING_RATIO:-0.08}"
export PATHOVISION_VLM_MAX_IMAGE_EDGE="${PATHOVISION_VLM_MAX_IMAGE_EDGE:-1280}"
export PATHOVISION_VLM_MAX_IMAGE_PIXELS="${PATHOVISION_VLM_MAX_IMAGE_PIXELS:-1600000}"
export PATHOVISION_VLM_MAX_TOKENS="${PATHOVISION_VLM_MAX_TOKENS:-8192}"
export PATHOVISION_VLM_MAX_CONCURRENT_PER_MODEL="$VLLM_MAX_NUM_SEQS"
export PATHOVISION_YOLO_HALF="${PATHOVISION_YOLO_HALF:-1}"
export PATHOVISION_PRELOAD_LOCALIZATION_MODELS="${PATHOVISION_PRELOAD_LOCALIZATION_MODELS:-1}"
export PYTHONUNBUFFERED=1

write_state 0 "STARTING_VLM"

start_vllm \
  0 "$GEMMA_PORT" \
  "$STUDENT_ROOT/Gemma4/gemma-4-31B-it" \
  "google/gemma-4-31B-it" \
  "gemma4-vllm" \
  --limit-mm-per-prompt '{"image":4}'
GEMMA_PID="$LAST_PID"

start_vllm \
  1 "$MISTRAL_PORT" \
  "$STUDENT_ROOT/Mistral-Small-3.1/mistral-small-3.1-24b-instruct-2503" \
  "mistralai/Mistral-Small-3.1-24B-Instruct-2503" \
  "mistral-vllm" \
  --tokenizer-mode mistral \
  --config-format mistral \
  --load-format mistral \
  --limit-mm-per-prompt '{"image":4}'
MISTRAL_PID="$LAST_PID"

# Phi is intentionally not started: its best_prompt/Prompt.md and optimized
# Cellular Skill are incomplete.  The requested Mistral/Gemma stack uses three GPUs.
unset PATHOVISION_PHI35_BASE_URL || true

write_state 0 "STARTING_REST_API"
CUDA_VISIBLE_DEVICES=2 "$SERVER_PYTHON" -m uvicorn pathovision_server:app \
  --app-dir "$PROJECT_DIR/server" \
  --host 0.0.0.0 \
  --port "$API_PORT" \
  --workers 1 >"$RUNTIME_DIR/server-${JOB_ID}.log" 2>&1 &
API_PID="$!"
pids+=("$API_PID")

API_READY=0
for _ in $(seq 1 180); do
  if ! kill -0 "$API_PID" 2>/dev/null; then
    tail -n 120 "$RUNTIME_DIR/server-${JOB_ID}.log" >&2 || true
    exit 1
  fi
  if "$SERVER_PYTHON" - "$API_PORT" <<'PY'
import sys
import urllib.request
try:
    with urllib.request.urlopen(
        f"http://127.0.0.1:{int(sys.argv[1])}/healthz", timeout=1.5
    ) as response:
        raise SystemExit(0 if response.status == 200 else 1)
except Exception:
    raise SystemExit(1)
PY
  then
    API_READY=1
    break
  fi
  sleep 2
done

if [ "$API_READY" -ne 1 ]; then
  tail -n 120 "$RUNTIME_DIR/server-${JOB_ID}.log" >&2 || true
  exit 1
fi

# REST/YOLO becomes usable immediately.  Large Student VLMs keep warming in
# the background and appear in /api/v1/student-models as each endpoint responds.
write_state 1 "REST_READY_VLM_LOADING"

VLM_FAILURES=0
if ! wait_for_model "$GEMMA_PORT" "$GEMMA_PID" "Gemma 4"; then
  VLM_FAILURES=$((VLM_FAILURES + 1))
fi
if ! wait_for_model "$MISTRAL_PORT" "$MISTRAL_PID" "Mistral Small 3.1"; then
  VLM_FAILURES=$((VLM_FAILURES + 1))
fi
if [ "$VLM_FAILURES" -eq 0 ]; then
  write_state 1 "READY"
else
  write_state 1 "REST_READY_VLM_PARTIAL"
fi

wait "$API_PID"
