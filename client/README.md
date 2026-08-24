# PathoVision Windows Client — Two-stage YOLO + ROI 分析推論模型

This localhost Gradio Client controls the NANO4 REST service through the existing
SSH/SOCKS session. Images, model weights, GPU inference, and case artifacts stay
on NANO4.

## User workflow

1. In **01 Image localization and analysis**, select the faster `YOLO11s`
   or highest-accuracy `YOLO11m` and run abnormal-region localization.
2. Select one or more matching regions (up to four by default). The upload image
   immediately highlights only the selected boxes while inference always keeps
   and uses a pristine unannotated source image.
3. Select the faster `Mistral Small 3.1 24B` or stronger-reasoning `Gemma4 31B`
   and analyze only the selected abnormal regions. Every ROI is submitted as an
   independent inference; up to two run concurrently on the selected vLLM.
4. Open **02 Structured visual report** in reading mode. Use the independent model
   and abnormal-region dropdowns to select any completed model × ROI report. Each
   report has bilingual Chinese/English field labels, professional Traditional
   Chinese values, and enlarged finding evidence. The report page is read-only.
5. In **03 Case records**, hover to highlight a row and right-click for
   explicit load, field-edit, or whole-record delete actions. Ordinary cell clicks
   do not load records. New creates and loads a blank backend record.

The first request always forces YOLO-only mode. The second request sends only
the selected detection indices; the Server performs the crops from its stored
original, runs each crop as a separate schema-constrained inference, and stores
one report artifact per ROI. When no abnormal region is detected, the ROI selector
and structured analysis action remain disabled and no analysis-model request is made.

## Automatic NANO4 allocation

The Client submits three GPUs and runs
`2026_NCHC_Summer_Intern_Project/slurm/pathovision_vlm_stack.sbatch`: one GPU each for Gemma,
Mistral, and FastAPI/YOLO. NANO4 permits at most 12 CPUs per GPU, so the UI caps
the three-GPU job at 36 CPUs and uses 32 by default.

Scheduler calls are timeout-bounded and runtime readiness is polled
independently. Gemma and Mistral load in parallel with safetensor prefetch and
multimodal memory profiling skipped on the high-memory NANO4 GPUs. As soon as the
Slurm allocation reaches `RUNNING`, the analysis workspace opens. Its in-page
progress panel reports REST Server, Mistral, and Gemma readiness every two
seconds; each model is added to the selector as soon as its inference endpoint
is live. Manual refresh remains available. Closing the Client or using the
session-end action cancels a job submitted by this Client and returns its
resources.

The analysis-inference stack uses compilation, CUDA Graphs, the interactivity performance mode,
prefix caching, and up to two dynamically batched requests per model. GPU YOLO
uses FP16. These optimize recurring inference; the first model start performs a
one-time compile/warm-up and may take longer.

## Install on the user's Windows localhost

```powershell
cd client
py -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python app.py
```

Use `--no-mcp` when the optional local MCP façade is not needed. The NANO4 host and SSH port shown on the login page are fixed. Enter the NANO4
account credentials and complete 2FA, then select the project directory and submit
the Slurm job. The Server project on NANO4 must contain the current `server/`, `slurm/`, `Localization_model/`, and
`Student_model/` directories. No model weights are copied to localhost.
