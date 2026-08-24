"""Localhost-only Gradio client for the PathoVision NANO4 REST API."""

from __future__ import annotations

import argparse
import base64
import html
import json
import os
import queue
import threading
import time
from collections import Counter
from io import BytesIO
from pathlib import Path
from typing import Any, Iterator

import gradio as gr
from PIL import Image, ImageDraw, ImageOps

from api_client import APIClientError, PathoVisionAPI
try:
    from mcp_server import clear_api as clear_mcp_api
    from mcp_server import set_api as set_mcp_api
    from mcp_server import start_mcp_server
except ModuleNotFoundError as exc:
    if exc.name != "mcp":
        raise

    def clear_mcp_api(_api: PathoVisionAPI | None = None) -> None:
        return None

    def set_mcp_api(_api: PathoVisionAPI) -> None:
        return None

    def start_mcp_server(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("MCP dependency is not installed; use --no-mcp or install requirements.txt.")

from nchc_remote import (
    NCHC_2FA_METHODS,
    RemoteError,
    close_all_sessions,
    close_session,
    create_managed_session,
    establish_tunnel,
    get_session,
    submit_server_job,
    wait_and_establish_tunnel,
)

APP_DIR = Path(__file__).resolve().parent
CSS_PATH = APP_DIR / "styles.css"
DEFAULT_NCHC_HOST = os.environ.get("NCHC_NANO4_HOST", "nano4.nchc.org.tw")

_CONNECTIONS: dict[str, PathoVisionAPI] = {}
YOLO_ONLY_STUDENT_MODEL = "__yolo_only__"
MAX_SELECTED_VLM_REGIONS = 4

LOCALIZATION_MODEL_LABELS = {
    "yolo11s": "YOLO11s　推論快且較準確",
    "yolo11m": "YOLO11m　推論稍慢且最準確",
}
STUDENT_MODEL_LABELS = {
    "mistral-small-3.1": "Mistral Small 3.1 24B　推論較快但理解及推理次佳",
    "gemma4": "Gemma4 31B　推論較慢但理解及推理最佳",
}

CLIENT_JS = r"""() => {
  if (window.__pathovisionCaseMenuInstalled) return [];
  window.__pathovisionCaseMenuInstalled = true;
  const menu = document.createElement('div');
  menu.id = 'pathovision-case-context-menu';
  menu.setAttribute('role', 'menu');
  menu.innerHTML = [
    '<button data-action="load" role="menuitem">載入這筆紀錄</button>',
    '<button data-action="edit" role="menuitem">編輯此筆所有欄位</button>',
    '<button data-action="delete" role="menuitem" class="danger">刪除整筆紀錄</button>'
  ].join('');
  document.body.appendChild(menu);
  let selectedRow = null;
  let selectedCaseId = '';
  const hide = () => { menu.style.display = 'none'; };
  const findRow = (event) => {
    const path = typeof event.composedPath === 'function' ? event.composedPath() : [];
    return path.find((node) =>
      node instanceof HTMLElement &&
      node.tagName === 'TR' &&
      node.closest('#server-case-history')
    ) || event.target?.closest?.('#server-case-history tbody tr');
  };
  const setBridgeValue = (value) => {
    const root = document.getElementById('context-case-id-bridge');
    const input = root?.matches?.('input, textarea') ? root : root?.querySelector('input, textarea');
    if (!input) return false;
    const prototype = input instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype
      : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(prototype, 'value')?.set;
    if (setter) setter.call(input, value); else input.value = value;
    input.dispatchEvent(new Event('input', {bubbles: true}));
    input.dispatchEvent(new Event('change', {bubbles: true}));
    return true;
  };
  const clickBridgeButton = (id) => {
    const root = document.getElementById(id);
    const button = root?.matches?.('button') ? root : root?.querySelector('button');
    button?.click();
  };
  const triggerAction = (action) => {
    if (!selectedCaseId || !setBridgeValue(selectedCaseId)) {
      window.alert('無法讀取所選個案編號，請重新整理紀錄後再試。');
      return;
    }
    const target = {
      load: 'context-load-case-button',
      edit: 'context-edit-case-button',
      delete: 'context-delete-case-button'
    }[action];
    window.setTimeout(() => clickBridgeButton(target), 80);
  };
  document.addEventListener('click', (event) => {
    if (!menu.contains(event.target)) hide();
  });
  document.addEventListener('contextmenu', (event) => {
    const row = findRow(event);
    if (!row) return;
    const firstCell = row.querySelector('td');
    const caseId = firstCell?.innerText?.trim() || '';
    if (!caseId) return;
    event.preventDefault();
    event.stopPropagation();
    selectedRow?.classList.remove('pv-context-selected');
    selectedRow = row;
    selectedRow.classList.add('pv-context-selected');
    selectedCaseId = caseId;
    menu.style.left = `${Math.max(8, Math.min(event.clientX, window.innerWidth - 230))}px`;
    menu.style.top = `${Math.max(8, Math.min(event.clientY, window.innerHeight - 150))}px`;
    menu.style.display = 'grid';
  }, true);
  menu.addEventListener('contextmenu', (event) => event.preventDefault());
  menu.addEventListener('click', (event) => {
    const action = event.target?.dataset?.action;
    if (!action || !selectedRow) return;
    hide();
    if (action === 'delete') {
      if (!window.confirm(`確定刪除個案「${selectedCaseId}」？此操作無法復原。`)) return;
    }
    triggerAction(action);
  });
  window.addEventListener('resize', hide);
  window.addEventListener('scroll', hide, true);
  return [];
}"""

CLIENT_CSS = r"""
#connection-shell { max-width: 980px; margin: 2vh auto 5vh; }
.connection-card { padding: 24px !important; border: 1px solid #dfe5f0 !important; border-radius: 20px !important; background: rgba(255,255,255,.96) !important; }
.connection-hero { padding: 22px; border-radius: 18px; color: white; background: linear-gradient(128deg,#07142d,#243e8a); margin-bottom: 16px; }
.connection-hero h1 { margin: 4px 0; color: white; }
.connection-hero p { margin: 0; color: #cbd7f4; }
.connection-note { padding: 12px 14px; border-radius: 12px; background: #f3f6ff; color: #596780; font-size: 12px; }
#api-session-toolbar {
    align-items: center;
    justify-content: flex-end;
    margin: 14px 0 18px;
    padding: 0;
    border: 0;
    background: transparent;
    box-shadow: none;
}
#disconnect-resource-button {
    flex: 0 1 420px;
    min-width: 300px;
    min-height: 48px;
    border: 1px solid #fecaca !important;
    border-radius: 12px !important;
    background: linear-gradient(135deg, #fff7f7, #fee2e2) !important;
    color: #b42318 !important;
    font-size: 15px !important;
    font-weight: 850 !important;
    box-shadow: 0 8px 20px rgba(180, 35, 24, .08) !important;
}
.record-toolbar {
    align-items: end;
    margin-top: 15px;
    padding: 16px;
    border: 1px solid #e1e7f1;
    border-radius: 17px;
    background: rgba(255, 255, 255, .9);
}
"""


def update_otp(method_label: str) -> Any:
    code = NCHC_2FA_METHODS.get(method_label, "1")
    if code == "2":
        return gr.update(visible=False, value="", label="Push 模式不需輸入 OTP")
    return gr.update(
        visible=True,
        value="",
        label="Mobile APP OTP" if code == "1" else "Email OTP",
    )


def api_for(token: str) -> PathoVisionAPI:
    api = _CONNECTIONS.get(token)
    if api is None:
        raise gr.Error("REST API 連線不存在，請重新連線。")
    return api


def localization_model_update(model_info: dict[str, Any]) -> Any:
    models = model_info.get("localization_models", [])
    choices = [
        (
            LOCALIZATION_MODEL_LABELS.get(
                str(item.get("key", "")),
                str(item.get("display_name", item.get("key", "YOLO"))),
            ),
            str(item.get("key", "")),
        )
        for item in models
        if item.get("ready") and item.get("key")
    ]
    available = {value for _label, value in choices}
    default = str(model_info.get("key", ""))
    if default not in available:
        default = choices[0][1] if choices else None
    return gr.update(choices=choices, value=default, interactive=bool(choices))


def student_model_update(
    api: PathoVisionAPI,
    model_info: dict[str, Any],
    preferred_model: str | None = None,
) -> Any:
    """Build a dropdown containing only structured-analysis endpoints that are live."""
    models = model_info.get("student_models")
    if not isinstance(models, list):
        models = api.student_models()
    choices = [
        (
            STUDENT_MODEL_LABELS.get(
                str(item.get("key", "")),
                str(item.get("display_name", item.get("key", "結構化分析模型"))),
            ),
            str(item.get("key", "")),
        )
        for item in models
        if item.get("inference_ready") and item.get("key")
    ]
    ready_keys = {value for _label, value in choices}
    default = str(preferred_model or model_info.get("default_student_model", ""))
    if default not in ready_keys:
        default = choices[0][1] if choices else None
    return gr.update(choices=choices, value=default, interactive=bool(choices))


def refresh_student_models(token: str, preferred_model: str | None = None) -> Any:
    """Refresh analysis-inference model readiness after background loading finishes."""
    api = api_for(token)
    try:
        return student_model_update(api, api.model(), preferred_model)
    except APIClientError as exc:
        raise gr.Error(str(exc)) from exc


def poll_student_models(token: str, preferred_model: str | None = None) -> Any:
    """Silently populate the dropdown when a background vLLM becomes ready."""
    if not token or token not in _CONNECTIONS:
        return gr.update()
    api = _CONNECTIONS[token]
    try:
        return student_model_update(api, api.model(), preferred_model)
    except APIClientError:
        return gr.update()


def model_loading_status_html(
    rest_ready: bool = False,
    model_info: dict[str, Any] | None = None,
) -> str:
    """Render honest service-level progress without inventing weight percentages."""
    students = {
        str(item.get("key", "")): item
        for item in (model_info or {}).get("student_models", [])
        if isinstance(item, dict) and item.get("key")
    }
    cards: list[tuple[str, str, str, str]] = [
        (
            "REST Server",
            "已連線" if rest_ready else "連線建立中",
            "YOLO 與個案 API 可使用" if rest_ready else "Compute Node 已配置，正在等待 API 回應",
            "ready" if rest_ready else "loading",
        )
    ]
    model_labels = (
        ("mistral-small-3.1", "Mistral Small 3.1 24B", "推論較快"),
        ("gemma4", "Gemma4 31B", "理解與推理最佳"),
    )
    for key, label, capability in model_labels:
        item = students.get(key)
        if item and item.get("inference_ready"):
            status, detail, state = "已就緒", f"{capability}；可開始結構化分析", "ready"
        elif item and not item.get("assets_ready"):
            status, detail, state = "模型資源不完整", "請檢查模型權重、best prompt 與 best skill", "error"
        elif item and item.get("endpoint_configured"):
            status, detail, state = "載入與暖機中", f"{capability}；完成後會自動啟用", "loading"
        elif rest_ready and item:
            status, detail, state = "等待模型服務", f"{capability}；正在等候 GPU 服務啟動", "loading"
        else:
            status, detail, state = "等待 REST 回報", f"{capability}；稍後自動更新", "waiting"
        cards.append((label, status, detail, state))

    ready_count = sum(state == "ready" for _label, _status, _detail, state in cards)
    percent = round(ready_count / len(cards) * 100)
    card_html = "".join(
        (
            f'<div class="model-load-item is-{state}">'
            f'<div><strong>{html.escape(label)}</strong><small>{html.escape(detail)}</small></div>'
            f'<span class="model-load-state">{html.escape(status)}</span></div>'
        )
        for label, status, detail, state in cards
    )
    return (
        '<section class="model-loading-panel">'
        '<div class="model-load-head"><div><span>運算服務載入狀態</span>'
        '<strong>模型服務準備進度</strong></div>'
        f'<b>{ready_count}/{len(cards)} 已就緒</b></div>'
        '<div class="model-load-track" role="progressbar" aria-label="模型服務準備進度" '
        f'aria-valuemin="0" aria-valuemax="100" aria-valuenow="{percent}">'
        f'<i style="width:{percent}%"></i></div>'
        f'<div class="model-load-grid">{card_html}</div>'
        '<p>頁面可先使用；各模型完成載入後，選單與對應功能會自動啟用。</p>'
        '</section>'
    )


def structured_model_loading_status(token: str) -> str:
    """Poll REST and both configured analysis-inference endpoints for the progress view."""
    if not token or token not in _CONNECTIONS:
        return model_loading_status_html()
    api = _CONNECTIONS[token]
    try:
        return model_loading_status_html(True, api.model())
    except APIClientError:
        return model_loading_status_html()


def selected_region_controls(
    case_id: str,
    student_model: str | None,
    selected_regions: list[str] | None,
) -> Any:
    return gr.update(
        interactive=bool(case_id and student_model and selected_regions)
    )


def draw_selected_regions(
    image: Image.Image,
    detections: list[dict[str, Any]],
    selected_regions: list[str] | None,
) -> Image.Image:
    """Draw only user-selected YOLO boxes on a fresh copy of the original image."""
    selected = {str(value) for value in selected_regions or []}
    preview = ImageOps.exif_transpose(image).convert("RGB").copy()
    if not selected:
        return preview
    draw = ImageDraw.Draw(preview)
    line_width = max(3, round(min(preview.size) / 180))
    for item in detections:
        index = str(item.get("index", ""))
        if index not in selected:
            continue
        raw_box = item.get("bbox_xyxy", [])
        if not isinstance(raw_box, list) or len(raw_box) < 4:
            continue
        x1, y1, x2, y2 = [round(float(value)) for value in raw_box[:4]]
        draw.rectangle((x1, y1, x2, y2), outline=(239, 35, 60), width=line_width)
        label = f"區域 {index}"
        label_box = draw.textbbox((x1, y1), label)
        label_height = label_box[3] - label_box[1] + 8
        label_width = label_box[2] - label_box[0] + 10
        top = max(0, y1 - label_height)
        draw.rectangle((x1, top, x1 + label_width, top + label_height), fill=(239, 35, 60))
        draw.text((x1 + 5, top + 4), label, fill="white")
    return preview


def selected_region_preview(
    source_image: Image.Image | None,
    detections: list[dict[str, Any]] | None,
    case_id: str,
    student_model: str | None,
    selected_regions: list[str] | None,
) -> tuple[Any, Any]:
    """Update the ROI overlay from local state without a REST round trip."""
    button = selected_region_controls(case_id, student_model, selected_regions)
    if not isinstance(source_image, Image.Image):
        return button, gr.update()
    return button, draw_selected_regions(
        source_image, detections or [], selected_regions
    )


def ssh_login_and_discover(
    host: str,
    port: int,
    username: str,
    password: str,
    method: str,
    otp: str,
    local_port: int,
) -> tuple[Any, ...]:
    try:
        session = create_managed_session(
            host, int(port), username, password, method, otp, int(local_port or 0)
        )
    except Exception as exc:
        return "", gr.update(choices=[], value=None), gr.update(choices=[], value=None), "", "", f"### SSH 登入失敗\n{html.escape(str(exc))}"
    project_choices = session.projects
    account_choices = session.accounts
    status = (
        f"### SSH 登入成功\n帳號 `{html.escape(session.username)}`；"
        f"找到 **{len(project_choices)}** 個 REST Server 專案。"
    )
    if not project_choices:
        status += "\n請先將本套件放到 `$HOME` 或 `/work/$USER`，且保留 `server/pathovision_server.py`。"
    return (
        session.token,
        gr.update(choices=project_choices, value=project_choices[0] if project_choices else None),
        gr.update(choices=account_choices, value=account_choices[0] if account_choices else None),
        "",
        "",
        status,
    )


def submit_and_connect(
    session_token: str,
    project_dir: str,
    partition: str,
    account: str,
    walltime: str,
    cpus: int,
    memory_gb: int,
    gpu_count: int,
    local_port: int,
    progress: gr.Progress = gr.Progress(),
) -> Iterator[tuple[Any, ...]]:
    try:
        partition = (partition or "").strip()
        if partition not in {"dev", "8gpus"}:
            raise gr.Error(f"NANO4 不支援 Partition：{partition}。請選擇 dev 或 8gpus。")
        session = get_session(session_token)
        progress(0.05, desc="上傳 Slurm 腳本")
        job_id = submit_server_job(
            session,
            project_dir,
            partition,
            account or "",
            walltime,
            int(cpus),
            int(memory_gb),
            int(gpu_count),
        )
        progress(0.15, desc=f"Job {job_id} 已提交，等待 Compute Node")
        wait_started = time.monotonic()
        server_started_at: float | None = None

        def update_progress(message: str) -> None:
            nonlocal server_started_at
            elapsed = max(0.0, time.monotonic() - wait_started)
            state = message.split(" · ", 1)[0]
            scheduler_states = {
                "SUBMITTED",
                "PENDING",
                "CONFIGURING",
                "REQUEUED",
                "REQUEUE_FED",
                "REQUEUE_HOLD",
                "RESIZING",
                "SCHEDULER_QUERY_RETRY",
                "UNKNOWN",
            }
            waiting_for_scheduler = state in scheduler_states and server_started_at is None
            if waiting_for_scheduler:
                startup_progress = min(0.38, 0.15 + elapsed / 1800.0 * 0.23)
                elapsed_label = f"Slurm 排隊／查詢已等待 {int(elapsed)} 秒"
            else:
                if server_started_at is None:
                    server_started_at = time.monotonic()
                server_elapsed = max(0.0, time.monotonic() - server_started_at)
                startup_progress = min(0.92, 0.42 + server_elapsed / 1800.0 * 0.50)
                elapsed_label = f"Server／分析推論模型啟動已等待 {int(server_elapsed)} 秒"
            progress(
                startup_progress,
                desc=f"{message} · {elapsed_label}",
            )

        def endpoint_is_ready(endpoint: str) -> bool:
            """Use the real API as the readiness source when the env flag lags."""
            probe = PathoVisionAPI(
                endpoint,
                session.api_key,
                timeout=3.0,
                proxy_url=session.proxy_url,
            )
            try:
                probe.health()
            except APIClientError:
                return False
            return True

        startup_updates: queue.Queue[str] = queue.Queue()
        startup_done = threading.Event()
        startup_result: dict[str, Any] = {}

        def wait_for_endpoint() -> None:
            try:
                startup_result["base_url"] = wait_and_establish_tunnel(
                    session,
                    int(local_port),
                    timeout_seconds=1800,
                    on_update=startup_updates.put,
                    endpoint_probe=endpoint_is_ready,
                )
            except Exception as exc:
                startup_result["error"] = exc
            finally:
                startup_done.set()

        wait_thread = threading.Thread(target=wait_for_endpoint, daemon=True)
        wait_thread.start()

        while not startup_done.is_set() or not startup_updates.empty():
            try:
                message = startup_updates.get(timeout=0.25)
            except queue.Empty:
                continue

            update_progress(message)
            state = message.split(" · ", 1)[0]
            if state not in {"RUNNING", "CONNECTED"}:
                continue

            node = session.node or "已配置"
            yield (
                session.token,
                gr.update(visible=False),
                gr.update(visible=True),
                (
                    "**Compute Node 已配置，REST Server／分析推論模型啟動中** · "
                    f"Job `{job_id}` · Node `{html.escape(node)}` · "
                    f"{html.escape(message)}"
                ),
                "### Compute Node 已配置\nREST Server／分析推論模型尚在初始化，完成後會自動啟用操作按鈕。",
                "",
                "",
                gr.update(choices=[], value=None, interactive=False),
                gr.update(choices=[], value=None, interactive=False),
                gr.update(interactive=False),
                gr.update(interactive=False),
                gr.update(interactive=False),
                gr.update(interactive=False),
            )

        wait_thread.join(timeout=0)
        startup_error = startup_result.get("error")
        if startup_error is not None:
            raise startup_error
        base_url = str(startup_result["base_url"])

        yield (
            session.token,
            gr.update(visible=False),
            gr.update(visible=True),
            "",
            "### Compute Node 已配置\nREST API 已可連線；結構化分析模型會在分析頁繼續載入。",
            "",
            "",
            gr.update(choices=[], value=None, interactive=False),
            gr.update(choices=[], value=None, interactive=False),
            gr.update(interactive=False),
            gr.update(interactive=False),
            gr.update(interactive=False),
            gr.update(interactive=False),
        )
        progress(0.94, desc="REST API 已回應，正在開啟分析功能")
        api = PathoVisionAPI(base_url, session.api_key, proxy_url=session.proxy_url)
        health = api.health()
        model = api.model()
        localization_update = localization_model_update(model)
        student_update = student_model_update(api, model)
        connection_token = session.token
        _CONNECTIONS[connection_token] = api
        set_mcp_api(api)
        progress(1.0, desc="REST Server 已連線")
        banner = ""
        status = (
            f"### 連線完成\nHealth：`{health.get('status')}`　"
            f"API：`{base_url}`　SOCKS：`127.0.0.1:{session.local_port}`"
        )
        yield (
            connection_token,
            gr.update(visible=False),
            gr.update(visible=True),
            banner,
            status,
            "",
            "",
            localization_update,
            student_update,
            gr.update(interactive=True),
            gr.update(interactive=True),
            gr.update(interactive=True),
            gr.update(interactive=True),
        )
    except Exception as exc:
        yield (
            "",
            gr.update(visible=True),
            gr.update(visible=False),
            "",
            f"### 啟動或連線失敗\n{html.escape(str(exc))}",
            "",
            "",
            gr.update(choices=[], value=None, interactive=False),
            gr.update(choices=[], value=None, interactive=False),
            gr.update(interactive=False),
            gr.update(interactive=False),
            gr.update(interactive=False),
            gr.update(interactive=False),
        )


def retry_tunnel(session_token: str, local_port: int) -> tuple[Any, ...]:
    try:
        session = get_session(session_token)
        base_url = establish_tunnel(session, int(local_port))
        api = PathoVisionAPI(base_url, session.api_key, proxy_url=session.proxy_url)
        api.health()
        model = api.model()
        _CONNECTIONS[session_token] = api
        set_mcp_api(api)
        return (
            session_token,
            gr.update(visible=False),
            gr.update(visible=True),
            "",
            f"### OpenSSH SOCKS 已連線\nAPI：`{base_url}`",
            localization_model_update(model),
            student_model_update(api, model),
            gr.update(interactive=True),
            gr.update(interactive=True),
            gr.update(interactive=True),
            gr.update(interactive=True),
        )
    except Exception as exc:
        return (
            "",
            gr.update(),
            gr.update(),
            "",
            f"### 尚未就緒\n{html.escape(str(exc))}",
            gr.update(choices=[], value=None, interactive=False),
            gr.update(choices=[], value=None, interactive=False),
            gr.update(interactive=False),
            gr.update(interactive=False),
            gr.update(interactive=False),
            gr.update(interactive=False),
        )


def connect_existing(base_url: str, api_key: str) -> tuple[Any, ...]:
    token = "direct:" + os.urandom(12).hex()
    try:
        api = PathoVisionAPI(base_url, api_key)
        health = api.health()
        model = api.model()
        _CONNECTIONS[token] = api
        set_mcp_api(api)
    except Exception as exc:
        return (
            "",
            gr.update(visible=True),
            gr.update(visible=False),
            "",
            f"### 連線失敗\n{html.escape(str(exc))}",
            gr.update(choices=[], value=None, interactive=False),
            gr.update(choices=[], value=None, interactive=False),
            gr.update(interactive=False),
            gr.update(interactive=False),
            gr.update(interactive=False),
            gr.update(interactive=False),
        )
    return (
        token,
        gr.update(visible=False),
        gr.update(visible=True),
        "",
        f"### 連線完成\nHealth：`{health.get('status')}`",
        localization_model_update(model),
        student_model_update(api, model),
        gr.update(interactive=True),
        gr.update(interactive=True),
        gr.update(interactive=True),
        gr.update(interactive=True),
    )


def disconnect(connection_token: str) -> tuple[Any, ...]:
    """中斷 Client，並自動取消由本程式提交的 Slurm Job。"""
    api = _CONNECTIONS.pop(connection_token, None)
    if api is not None:
        clear_mcp_api(api)
    cancelled = False
    if connection_token and not connection_token.startswith("direct:"):
        try:
            close_session(connection_token, cancel_job=True)
            cancelled = True
        except Exception:
            # UI 中斷不能因清理例外而卡死；遠端 Job 仍受程式退出清理保護。
            pass
    message = (
        "已中斷連線，並已送出 scancel 歸還 NANO4 運算資源。"
        if cancelled
        else "已中斷 Client 與 REST Server 的連線。"
    )
    return (
        "",
        gr.update(visible=True),
        gr.update(visible=False),
        "",
        message,
    )


def rows_from_metadata(metadata: dict[str, Any]) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for item in metadata.get("detections", []):
        box = item.get("bbox_xyxy", [0, 0, 0, 0])
        rows.append(
            [
                int(item.get("index", len(rows) + 1)),
                str(item.get("class_name", "unknown")),
                round(float(item.get("confidence", 0.0)), 4),
                *[round(float(value), 1) for value in box[:4]],
            ]
        )
    return rows


def image_to_data_uri(image: Image.Image) -> str:
    preview = image.convert("RGB").copy()
    preview.thumbnail((1800, 1400), Image.Resampling.LANCZOS)
    buffer = BytesIO()
    preview.save(buffer, format="JPEG", quality=92, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def empty_metrics_html() -> str:
    return """
    <div class="metric-grid">
      <div class="metric-card"><span class="metric-label">候選異常區域</span><strong>—</strong><small>等待分析</small></div>
      <div class="metric-card"><span class="metric-label">最高信心分數</span><strong>—</strong><small>Confidence</small></div>
      <div class="metric-card"><span class="metric-label">類別</span><strong>—</strong><small>Class</small></div>
      <div class="metric-card"><span class="metric-label">影像解析度</span><strong>—</strong><small>Width × Height</small></div>
      <div class="metric-card"><span class="metric-label">分析推論模型</span><strong class="metric-text">—</strong><small>等待分析</small></div>
    </div>"""


def build_metrics(metadata: dict[str, Any]) -> str:
    analysis = metadata.get("analysis", {})
    image = metadata.get("image", {})
    count = int(analysis.get("candidate_count", 0))
    maximum = float(analysis.get("max_confidence", 0.0))
    top = html.escape(str(analysis.get("top_label", "未偵測")).replace("_", " "))
    vlm = analysis.get("student_vlm", {})
    vlm_status = str(vlm.get("status", "not_requested"))
    raw_model_names = vlm.get("model_names", [])
    if isinstance(raw_model_names, list) and raw_model_names:
        display_model_name = "、".join(str(name) for name in raw_model_names if name)
    else:
        display_model_name = str(vlm.get("model_name", "YOLO only"))
    vlm_name = html.escape(display_model_name)
    vlm_label = vlm_name if vlm_status in {"completed", "partial"} else html.escape(vlm_status)
    return f"""
    <div class="metric-grid">
      <div class="metric-card metric-accent"><span class="metric-label">候選異常區域</span><strong>{count}</strong><small>Detected regions</small></div>
      <div class="metric-card"><span class="metric-label">最高信心分數</span><strong>{maximum:.1%}</strong><small>Confidence</small></div>
      <div class="metric-card"><span class="metric-label">類別</span><strong class="metric-text">{top}</strong><small>Class</small></div>
      <div class="metric-card"><span class="metric-label">影像解析度</span><strong class="metric-text">{image.get('width',0)} × {image.get('height',0)}</strong><small>Width × Height</small></div>
      <div class="metric-card"><span class="metric-label">分析推論模型</span><strong class="metric-text">{vlm_label}</strong><small>{html.escape(vlm_status)}</small></div>
    </div>"""


SCHEMA_SECTION_LABELS = {
    "Analysis_Metadata": "分析資訊與技能狀態",
    "Image_Context": "影像與檢體脈絡",
    "Image_Quality": "影像品質與可判讀性",
    "Tissue_Components": "組織成分與分布",
    "Cellular_Cytoplasmic_and_Nuclear_Morphology": "細胞質與細胞核形態",
    "General_Tissue_Architecture": "組織結構與細胞排列",
    "Extracellular_Matrix_and_Stroma": "細胞外基質與間質形態",
    "Special_Findings": "特殊病理形態所見",
    "Conditional_Findings": "條件式專項分析",
    "Morphological_Summary": "整合形態學摘要",
    "Limitations": "分析限制與人工複核",
}


SCHEMA_FIELD_LABELS = {
    "Activated_Modules": "已啟用的專項分析模組",
    "Basement_Membrane_Assessability": "基底膜可評估性",
    "Architectural_Disorganization": "組織結構紊亂程度",
    "Architectural_Morphological_Deviation": "組織結構形態偏離",
    "Assessability": "可評估性",
    "Border_Completeness": "組織邊界完整性",
    "Border_Morphology": "組織邊界形態",
    "Border_Presence": "是否可見組織邊界",
    "Capsule_Like_Tissue": "包膜樣組織",
    "Cellular_Crowding": "細胞擁擠程度",
    "Cellular_Morphological_Deviation": "細胞形態偏離",
    "Entrapment_or_Interdigitation": "組織包埋或交錯現象",
    "Interface_with_Adjacent_Tissue": "與相鄰組織的介面",
    "Orientation_or_Polarity": "細胞排列方向或極性",
    "Reference_Adequacy": "參照組織充分性",
    "Reference_Context": "形態比較參照",
    "ROI_Edge_Limitation": "感興趣區域邊緣限制",
    "Supporting_Visible_Features": "支持性可見形態特徵",
    "Visible_Structural_Relationships": "可見組織結構關係",
    "Bridging_Tufting_or_Budding": "腔內橋接、簇狀突起或芽生",
    "Content": "腔內內容物",
    "Distortion_or_Dilation": "腔隙變形或擴張",
    "Distribution": "空間分布",
    "Dominant_Epithelial_Pattern": "主要上皮結構型態",
    "Epithelial_Component": "上皮性成分",
    "Epithelial_Layering": "上皮層次",
    "Glandular_or_Ductal_Organization": "腺體或導管組織方式",
    "Lining_Morphology": "腔面襯裡形態",
    "Luminal_Organization": "腔隙組織性",
    "Number": "數量",
    "Polarity_and_Orientation": "細胞極性與排列方向",
    "Possible_Artifact": "可能的影像偽影",
    "Secondary_Epithelial_Patterns": "次要上皮結構型態",
    "Shape": "形狀",
    "Size": "大小",
    "Space_Presence": "腔隙或空間是否存在",
    "Space_Type": "腔隙或空間類型",
    "Structural_Fusion_or_Complexity": "結構融合或複雜性",
    "Additional_Components": "次要組織成分",
    "Analysis_Metadata": "分析資訊與技能狀態",
    "Analysis_Purpose": "分析目的",
    "Analysis_Scope": "分析範圍",
    "Architectural_Organization": "組織結構完整性",
    "Artifacts": "影像偽影",
    "Calcification_or_Mineralization": "鈣化或礦化",
    "Cell_Borders": "細胞界限",
    "Cell_Shape": "細胞形狀",
    "Cell_Size": "細胞大小",
    "Cellular_Arrangement": "細胞排列方式",
    "Cellular_Cohesion": "細胞黏附性",
    "Cellular_Cytoplasmic_and_Nuclear_Morphology": "細胞質與細胞核形態",
    "Cellular_Debris": "細胞碎屑",
    "Cellularity_Distribution": "細胞密度分布",
    "Chromatin": "染色質特徵",
    "Component_Distribution": "組織成分分布",
    "Conditional_Findings": "條件式專項分析",
    "Conditional_Skills_Not_Performed": "未執行的條件式技能",
    "Cytoplasmic_Features": "細胞質特徵",
    "Direct_Observations_Only": "影像直接形態觀察",
    "Dominant_Pattern": "主要組織型態",
    "Effect_on_Analysis": "對分析判讀的影響",
    "Extracellular_Matrix_and_Stroma": "細胞外基質與間質形態",
    "Findings": "形態學所見",
    "Focus": "對焦品質",
    "General_Tissue_Architecture": "組織結構與細胞排列",
    "Human_Review_Reason": "建議人工複核原因",
    "Human_Review_Suggested": "是否建議人工複核",
    "Image_Context": "影像與檢體脈絡",
    "Image_Limitations": "影像品質限制",
    "Image_Quality": "影像品質與可判讀性",
    "Image_Type": "影像類型",
    "Inflammation": "發炎細胞浸潤",
    "Integrated_Analysis_Status": "整合分析狀態",
    "Limitations": "局部評估限制",
    "Loaded_Skills": "已載入的病理分析技能",
    "Loading_Status": "技能載入狀態",
    "Magnification_or_MPP": "放大倍率／每像素微米數（MPP）",
    "Matrix_Features": "基質形態特徵",
    "Mitotic_Figures": "有絲分裂象",
    "Module_Findings": "專項模組所見",
    "Morphological_Summary": "整合形態學摘要",
    "Necrosis_or_Cell_Death": "壞死或細胞死亡",
    "Not_Evaluable_Findings": "無法評估的形態所見",
    "Nuclear_Contour": "核膜輪廓",
    "Nuclear_Shape": "細胞核形狀",
    "Nuclear_Size": "細胞核大小",
    "Nuclear_Variation": "細胞核異型程度",
    "Nuclear_to_Cytoplasmic_Ratio": "核質比（N:C ratio）",
    "Organ_or_Site": "器官／解剖部位",
    "Other_Deposited_Material": "其他沉積物",
    "Overall_Assessability": "整體可判讀性",
    "Population_Uniformity": "細胞族群一致性",
    "Predominant_Component": "主要組織成分",
    "Prominent_Nucleoli": "明顯核仁",
    "Reason": "原因",
    "Resolution": "影像解析度",
    "ROI_Limitations": "感興趣區域（ROI）限制",
    "Sampling_Limitations": "取樣代表性限制",
    "Schema_Version": "結構版本",
    "Secondary_Patterns": "次要組織型態",
    "Skill_Name": "技能名稱",
    "Skill_Path": "技能來源路徑",
    "Spatial_Organization": "空間組織方式",
    "Special_Findings": "特殊病理形態所見",
    "Stain": "染色方法",
    "Staining_Quality": "染色品質",
    "Status": "判讀狀態",
    "Stromal_Amount": "間質量",
    "Stromal_Cellularity": "間質細胞密度",
    "Stromal_Density": "間質緻密度",
    "Stromal_Type": "間質類型",
    "Structural_Complexity": "結構複雜度",
    "Supporting_Visible_Evidence": "影像可見依據",
    "Tissue_Components": "組織成分與分布",
    "Tissue_Coverage": "組織涵蓋範圍",
    "Unavailable_Skills": "無法使用的病理分析技能",
    "Uncertain_Findings": "不確定的形態所見",
    "Value": "形態觀察結果",
    "Vascular_or_Hemorrhagic_Features": "血管、出血或循環相關所見",
}


SKILL_DISPLAY_LABELS = {
    "Specimen_Image_and_Stain_Context": "檢體影像與染色脈絡分析",
    "Pathology_Image_Quality_and_Structured_Morphology_Reporting": "病理影像品質與結構化形態報告",
    "Tissue_Component_Recognition": "組織成分辨識",
    "Cellular_Cytoplasmic_and_Nuclear_Morphology": "細胞質與細胞核形態分析",
    "General_Tissue_Architecture_Analysis": "一般組織結構分析",
    "Extracellular_Matrix_and_Stromal_Morphology": "細胞外基質與間質形態分析",
    "Vascular_Hemorrhagic_and_Circulatory_Features": "血管、出血與循環形態分析",
    "Inflammation_and_Immune_Microenvironment": "發炎與免疫微環境分析",
    "Cell_Death_Mitotic_Activity_and_Deposition_Analysis": "細胞死亡、有絲分裂活性與沉積物分析",
    "Tissue_Border_Interface_and_Growth_Pattern": "組織邊界、介面與生長型態分析",
    "Lumina_Cysts_Channels_and_Tissue_Spaces_Analysis": "腔隙、囊腫、管道與組織空間分析",
    "Spatial_Heterogeneity_and_Multiregion_Analysis": "空間異質性與多區域分析",
    "Morphological_Deviation_and_Disorganization_Assessment": "形態偏離與結構紊亂評估",
    "Epithelial_and_Glandular_Architecture": "上皮與腺體結構分析",
    "Soft_Tissue_and_Mesenchymal_Patterns": "軟組織與間葉型態分析",
    "Lymphoid_and_Hematopoietic_Architecture": "淋巴與造血組織結構分析",
    "Neural_and_Glial_Tissue_Morphology": "神經與膠質組織形態分析",
    "Bone_Cartilage_and_Osteoid_Morphology": "骨、軟骨與類骨質形態分析",
    "Muscle_Tissue_Morphology": "肌肉組織形態分析",
    "Organ_Specific_Parenchymal_Morphology_Router": "器官特異性實質形態分析路由",
}


DISPLAY_TEXT_TRANSLATIONS = {
    "Present": "可見／存在",
    "Absent": "未見",
    "N/A": "不適用",
    "Not_Evaluable": "無法評估",
    "Indeterminate": "未能確定",
    "Unknown": "未知",
    "Available": "可用",
    "Partially_Available": "部分可用",
    "Unavailable": "無法使用",
    "Adequate": "足以判讀",
    "Assessable": "可評估",
    "ROI": "感興趣區域（ROI）",
    "Patch": "影像區塊",
    "WSI": "全視野數位切片（WSI）",
    "Non-diagnostic pathology image morphology assistance": "非診斷性病理影像形態輔助分析",
    "Provided image/ROI only": "僅限所提供的影像／感興趣區域（ROI）",
    "Central": "中央分布",
    "Concentric": "同心圓狀排列",
    "Empty/Clear": "空腔／內容清澈",
    "Epithelial": "上皮性",
    "Epithelial/Glandular": "上皮性／腺體性",
    "Fibrillar": "纖維狀",
    "Fibrous stroma": "纖維性間質",
    "Fibrous-appearing": "呈纖維性外觀",
    "High": "高",
    "Indistinct": "界限不清",
    "Low": "低",
    "Lumen": "腔隙",
    "Minimal": "輕微",
    "Moderate": "中等",
    "Multilayered/Pseudostratified": "多層／假複層排列",
    "None": "無",
    "Open": "開放",
    "Pale, indistinct": "淡染且界限不清",
    "Polygonal to oval": "多角形至卵圓形",
    "Preserved": "結構保存",
    "Pseudostratified to multilayered": "假複層至多層排列",
    "Round": "圓形",
    "Round to oval": "圓形至卵圓形",
    "Simple": "單純",
    "Simple Gland": "單純腺體",
    "Small": "小",
    "Small to medium": "小至中等",
    "Smooth": "平滑",
    "Tubular": "管狀",
    "Tubular/Glandular": "管狀／腺體狀",
    "Uniform": "均一",
    "Vesicular": "泡狀染色質",
    "Abrupt": "界面轉換截然",
    "Adjacent": "相鄰",
    "Good": "可清楚評估",
    "Limited": "評估受限",
    "Limited reference tissue": "參照組織有限",
    "Marked": "顯著",
    "Nuclear pleomorphism": "細胞核多形性",
    "Partial": "部分",
    "Partial border visibility": "僅部分組織邊界可見",
    "Peripheral fibrous stroma": "周邊纖維性間質",
    "Prominent nucleoli": "明顯核仁",
    "Pushing-appearing": "呈推擠性邊界外觀",
    "Solid growth pattern": "實性生長型態",
    "A clear interface exists between the cellular mass and the fibrous stroma on the right": "細胞性團塊與右側纖維性間質之間可見清楚介面",
    "Cells are densely packed with overlapping nuclei": "細胞緊密擁擠排列，細胞核相互重疊",
    "Cellular mass is directly adjacent to fibrous stroma": "細胞性團塊與纖維性間質直接相鄰",
    "Comparison is limited to the small area of stroma": "形態比較僅限於小範圍間質",
    "Complete loss of any organized tissue pattern": "完全喪失可辨識的規則組織排列",
    "Directly observed in the cellular mass": "上述特徵可於細胞性團塊中直接觀察",
    "Extreme difference in cell size, nuclear size, and cellularity compared to the stroma": "與間質相比，細胞大小、細胞核大小及細胞密度差異極為明顯",
    "High resolution allows for detailed nuclear assessment": "影像解析度足以進行細胞核細節評估",
    "No applicable directional organization in a solid sheet": "實性片狀細胞中無適用的方向性排列可供評估",
    "No distinct fibrous capsule separating the mass from the stroma": "團塊與間質之間未見明確纖維性包膜",
    "No entrapment of stromal elements within the cellular mass": "細胞性團塊內未見間質成分遭包埋",
    "No significant retraction or processing clefts observed": "未見明顯收縮裂隙或製片處理造成的裂隙",
    "No spaces identified": "未辨識到腔隙或組織空間",
    "No true lumina, cysts, or channels observed within the cellular mass": "細胞性團塊內未見真正腔隙、囊腔或管道結構",
    "Only one border is visible within the ROI": "感興趣區域內僅能觀察到單側組織邊界",
    "Only one edge of the mass is captured": "影像僅涵蓋團塊的一側邊緣",
    "Replacement of normal tissue structure by a solid sheet of epithelioid cells": "正常組織結構由實性片狀類上皮細胞取代",
    "Sharp transition from hypercellular mass to hypocellular stroma": "高細胞性團塊與低細胞性間質之間呈截然轉換",
    "Significant variation in nuclear size and shape": "細胞核大小與形狀具有明顯變異",
    "Stroma is only present in a small portion of the ROI": "間質僅占感興趣區域的一小部分",
    "The cellular mass meets the stroma in a relatively smooth, rounded interface": "細胞性團塊與間質交界相對平滑且呈圓鈍外觀",
    "The fibrous stroma provides a baseline for normal mesenchymal cellularity": "纖維性間質可作為一般間葉組織細胞密度的形態比較基準",
    "The mass extends beyond the left, top, and bottom edges of the ROI": "團塊延伸超出感興趣區域的左側、上方及下方邊界",
    "The visible interface is well-resolved": "可見介面的解析度良好，可供評估",
    "Tissue is well-preserved": "組織保存良好",
    "Central gland surrounded by peripheral stroma": "中央腺體結構，周邊由間質環繞",
    "Localized to the glandular wall": "局限於腺體壁",
    "Pale; low contrast between cytoplasm and background": "染色偏淡；細胞質與背景對比不足",
    "Single small glandular structure centered in ROI": "感興趣區域中央可見單一小型腺體結構",
    "Basement membrane integrity": "基底膜完整性",
    "Basement membrane not clearly resolved with current stain/resolution": "受限於目前染色與解析度，基底膜無法清楚辨識",
    "Cells appear packed in a circular arrangement": "細胞呈緊密環狀排列",
    "Cells are organized in a ring-like pattern around a central space": "細胞環繞中央空間呈環狀排列",
    "Cells are oriented radially around the lumen": "細胞沿腔面呈放射狀排列",
    "Cells are relatively uniform in size": "細胞大小相對均一",
    "Cells are tightly packed in a continuous layer": "細胞緊密排列並形成連續細胞層",
    "Cells organized in a glandular structure": "細胞排列形成腺體結構",
    "Central clear space within the cellular ring": "環狀細胞結構中央可見清澈腔隙",
    "Circular arrangement of cells surrounding a central lumen": "細胞環繞中央腔隙呈圓形排列",
    "Circular cross-section": "橫切面呈圓形",
    "Clear organization of cells around a central space": "細胞環繞中央空間，組織排列清楚",
    "Consistent morphology across the glandular lining": "腺體襯裡細胞形態一致",
    "Consistent nuclear size and shape across the population": "細胞族群中的核大小與形狀一致",
    "Cytoplasm is lightly stained and difficult to distinguish from the background": "細胞質淡染，與背景不易區分",
    "Detailed cytoplasmic inclusions": "細胞質內含物細節",
    "Eosinophilic, fibrillar material surrounding the glandular structure": "腺體結構周圍可見嗜伊紅性纖維狀物質",
    "Exact epithelial layering (pseudostratified vs. multilayered) due to 2D sectioning": "受二維切片限制，無法精確區分上皮為假複層或多層排列",
    "Few spindle-shaped nuclei visible in the stroma": "間質內僅見少量梭形細胞核",
    "Fibers are loosely packed": "纖維排列疏鬆",
    "Fibrillar material present around the periphery of the gland": "腺體周邊可見纖維狀物質",
    "Glandular structure is centrally located with stroma at the ROI edges": "腺體位於感興趣區域中央，間質分布於周緣",
    "High cellularity in the ring, low cellularity in the surrounding stroma": "環狀結構細胞密度高，周圍間質細胞密度低",
    "Lack of clear demarcation between adjacent cells": "相鄰細胞間缺乏清楚界限",
    "Linear, eosinophilic collagenous-like fibers": "可見線狀、嗜伊紅性、類膠原纖維",
    "Lined by a layer of polygonal cells": "腔面由多角形細胞襯裡",
    "Located in the center of the cellular cluster": "位於細胞群中央",
    "Low contrast staining": "染色對比不足",
    "Low contrast staining limits detailed cytoplasmic assessment": "染色對比不足，限制細胞質細節評估",
    "Lumen appears devoid of significant material": "腔內未見明顯內容物",
    "Lumen diameter is roughly equal to the thickness of the cellular wall": "腔徑約與細胞壁厚度相當",
    "Lumen is clear and unobstructed": "腔隙清楚且未見阻塞",
    "Lumen is well-defined and lined by cells, not a retraction artifact": "腔隙界限清楚並有細胞襯裡，不支持收縮偽影",
    "Lumen maintains a regular circular shape": "腔隙維持規則圓形",
    "Morphology is clear and consistent within the provided ROI.": "所提供感興趣區域內的形態清楚且一致。",
    "No adequate internal or external reference tissue is available for comparison.": "缺乏足夠的內部或外部參照組織可供比較。",
    "No areas of coagulative or liquefactive necrosis": "未見凝固性或液化性壞死區域",
    "No basophilic mineral deposits": "未見嗜鹼性礦物質沉積",
    "No bone or cartilage is visible.": "影像中未見骨或軟骨組織。",
    "No bridging of the lumen or budding of the wall": "未見跨腔橋接或腔壁芽生",
    "No clear tissue border or interface with adjacent distinct tissue is visible within the ROI.": "感興趣區域內未見清楚的組織邊界，亦未見與相鄰異質組織的介面。",
    "No distinct blood vessels or extravasated erythrocytes": "未見明確血管或血管外紅血球",
    "No distinct nucleoli visible at this resolution": "在目前解析度下未見明顯核仁",
    "No foreign material or pigments observed": "未見異物或色素沉積",
    "No fusion with other glandular structures": "未見與其他腺體結構融合",
    "No karyorrhectic debris visible": "未見核碎裂性細胞碎屑",
    "No lymphoid or hematopoietic tissue is visible.": "影像中未見淋巴或造血組織。",
    "No mitotic figures identified in the ROI": "感興趣區域內未辨識到有絲分裂象",
    "No muscle tissue is visible.": "影像中未見肌肉組織。",
    "No neural or glial tissue is visible.": "影像中未見神經或膠質組織。",
    "No visible lymphocytes, neutrophils, or plasma cells": "未見淋巴球、嗜中性球或漿細胞",
    "Nuclear membranes appear regular": "核膜輪廓規則",
    "Nuclei appear at different levels within the thickness of the wall": "細胞核位於腔壁厚度內的不同層次",
    "Nuclei are predominantly circular or slightly elongated": "細胞核主要呈圓形或輕度延長",
    "Nuclei are seen at multiple levels within the wall": "腔壁內可見多層次排列的細胞核",
    "Nuclei occupy a significant portion of the cell volume": "細胞核占細胞體積相當比例",
    "Nuclei show minimal size variation": "細胞核大小變異輕微",
    "Nuclei show open chromatin with some peripheral condensation": "細胞核呈開放性染色質，周邊可見部分濃縮",
    "Only a single ROI was supplied.": "僅提供單一感興趣區域。",
    "Organ identity was not explicitly supplied.": "未明確提供器官資訊。",
    "Predominant structure is epithelial/glandular.": "主要結構為上皮性／腺體性。",
    "Single ROI provided": "僅提供單一感興趣區域",
    "Single central lumen": "單一中央腔隙",
    "Single circular structure with a central lumen": "單一圓形結構，中央具有腔隙",
    "Single circular tube-like structure": "單一圓形管狀結構",
    "Single lumen with a regular cellular lining": "單一腔隙，具有規則細胞襯裡",
    "Single lumen without branching or fusion": "單一腔隙，未見分支或融合",
    "Small field of view limits assessment of overall tissue architecture": "視野範圍較小，限制整體組織結構評估",
    "Space is lined by epithelial cells": "空間表面由上皮細胞襯裡",
    "Visible linear arrangement of stromal fibers": "可見間質纖維呈線性排列",
    "The ROI shows a single, small, circular glandular structure centered in the field. The gland is lined by a multilayered or pseudostratified population of uniform, small-to-medium polygonal cells with round-to-oval nuclei and vesicular chromatin. The cells are tightly cohesive and oriented radially around a clear, open central lumen. The gland is surrounded by a moderate amount of loose, fibrillar, eosinophilic stroma with low cellularity. No inflammation, necrosis, or mitotic figures are observed.": "感興趣區域中央可見單一、小型、圓形腺體結構。腺體由形態均一的小至中型多角形細胞呈多層或假複層排列，細胞核為圓形至卵圓形，染色質呈泡狀。細胞黏附緊密，沿清楚且開放的中央腔隙呈放射狀排列。腺體周圍可見中等量、疏鬆、纖維狀且嗜伊紅性的低細胞性間質。未見發炎、壞死或有絲分裂象。",
    "Abundant, eosinophilic, granular": "豐富、嗜伊紅性且呈顆粒狀",
    "Adequate for cellular detail": "足以評估細胞層級細節",
    "Ample pink cytoplasm with a slightly granular texture": "細胞質豐富、呈粉紅色，質地略帶顆粒狀",
    "Background is clean of apoptotic bodies or debris": "背景未見明顯凋亡小體或細胞碎屑",
    "Cell boundaries are poorly defined due to high cellular density and crowding": "因細胞密度高且排列擁擠，細胞界限不清",
    "Cells are arranged in a solid, non-organized mass": "細胞呈實性團塊排列，缺乏規則組織結構",
    "Cells are closely apposed with minimal intervening space": "細胞彼此緊密貼附，間隙極少",
    "Cells are packed closely together without intervening stroma within the main mass": "主要團塊內細胞緊密擁擠，未見介入性間質",
    "Cells are significantly larger than typical lymphocytes": "細胞明顯大於一般淋巴球",
    "Cells are tightly packed in a cohesive sheet": "細胞緊密排列成具黏附性的片狀結構",
    "Cells exhibit varied polygonal shapes with some rounded profiles": "細胞呈多樣性多角形，部分輪廓較圓",
    "Chromatin is distributed unevenly with clear areas within the nucleus": "染色質分布不均，細胞核內可見淡染區",
    "Coarse, vesicular": "粗糙、泡狀",
    "Collagenous": "膠原纖維性",
    "Consistent eosinophilic and basophilic contrast": "嗜伊紅性與嗜鹼性染色對比一致",
    "Crowded": "擁擠",
    "Dense population of large cells with abundant cytoplasm and prominent nuclei occupying the majority of the field": "高密度大型細胞族群具豐富細胞質與顯著細胞核，占據大部分視野",
    "Disrupted": "結構中斷且紊亂",
    "Distinct, eosinophilic nucleoli are visible in multiple nuclei": "多個細胞核內可見清楚且嗜伊紅性的核仁",
    "Enlarged": "增大",
    "Eosinophilic collagenous tissue visible at the right margin of the image": "影像右側邊緣可見嗜伊紅性膠原纖維組織",
    "Eosinophilic, collagenous appearance of the peripheral tissue": "周邊組織呈嗜伊紅性膠原纖維外觀",
    "Epithelioid-appearing cellular sheet": "類上皮樣細胞片",
    "Few nuclei are visible within the fibrous stroma": "纖維性間質內僅見少量細胞核",
    "Fibrous": "纖維性",
    "Fibrous-appearing stroma": "呈纖維性外觀的間質",
    "Focal cellular mass adjacent to stroma": "局灶性細胞團塊鄰接間質",
    "Heterogeneous": "不均一",
    "High cell density is maintained throughout the ROI": "整個感興趣區域均維持高細胞密度",
    "Increased": "增高",
    "Irregular": "不規則",
    "Lack of complex structures like glands or follicles": "未見腺體或濾泡等複雜組織結構",
    "Mitotic activity is indeterminate due to resolution of dense chromatin clumps": "受限於緻密染色質團塊的解析度，有絲分裂活性無法確定",
    "Most nuclei are oval, but several show irregular contours": "多數細胞核呈卵圓形，部分核輪廓不規則",
    "No basophilic mineral deposits observed": "未見嗜鹼性礦物質沉積",
    "No evidence of native organ architecture": "未見原生器官組織結構",
    "No geographic necrosis or widespread karyorrhexis observed": "未見地圖狀壞死或廣泛核碎裂",
    "No significant infiltration of lymphocytes or neutrophils observed": "未見明顯淋巴球或嗜中性球浸潤",
    "No visible blood vessels or extravasated erythrocytes": "未見可辨識血管或血管外紅血球",
    "Nuclei are large relative to the amount of cytoplasm": "相對於細胞質量，細胞核偏大",
    "Nuclei are prominent and occupy a significant portion of the cell volume": "細胞核顯著，並占據相當比例的細胞體積",
    "Oval to irregular": "卵圓形至不規則形",
    "Partial border visibility; the mass extends beyond the ROI edges": "組織邊界僅部分可見；團塊延伸超出感興趣區域邊緣",
    "Sheet-like": "片狀",
    "Small ROI prevents assessment of overall lesion architecture or heterogeneity": "感興趣區域較小，無法評估病灶整體結構或異質性",
    "Solid growth": "實性生長",
    "Some dense chromatin clumps are present, but definitive mitotic figures are not clearly resolved": "可見部分緻密染色質團塊，但無法清楚辨識確切有絲分裂象",
    "Some nuclei exhibit indented or angular membranes": "部分細胞核呈核膜凹陷或稜角狀輪廓",
    "Stroma is limited to the right periphery of the image": "間質僅分布於影像右側周邊",
    "The cellular component forms a solid mass that terminates abruptly at the right edge against a fibrous band": "細胞性成分形成實性團塊，於右側與纖維帶交界處呈截然終止",
    "The image shows a hypercellular mass composed of large, polygonal epithelioid-appearing cells arranged in a solid, cohesive sheet. The cells exhibit abundant eosinophilic granular cytoplasm and enlarged, oval to irregular nuclei with coarse chromatin and prominent nucleoli. There is moderate nuclear pleomorphism. The mass is adjacent to a dense, hypocellular fibrous stroma, with a pushing-appearing interface. No clear lumina, inflammation, or necrosis are observed.": "影像顯示一高細胞性團塊，由大型、多角形、類上皮樣細胞排列成實性且具黏附性的片狀結構。細胞質豐富、嗜伊紅性且呈顆粒狀；細胞核增大，呈卵圓形至不規則形，染色質粗糙並具有明顯核仁。可見中度細胞核多形性。團塊鄰接緻密、低細胞性的纖維性間質，介面呈推擠性外觀。未見明確腔隙、發炎或壞死。",
    "The peripheral fibrous band appears compact": "周邊纖維帶呈緻密外觀",
    "To confirm mitotic count and evaluate the lesion in the context of the whole slide.": "建議人工複核有絲分裂象計數，並於全視野切片脈絡下評估病灶。",
    "Uniformly hypercellular": "均勻性高細胞密度",
    "Variation in nuclear size and shape is evident across the population": "細胞族群中可見明顯核大小與形狀變異",
    "Variation in nuclear size, shape, and nucleolar prominence": "細胞核大小、形狀及核仁顯著度具有變異",
    "Wavy eosinophilic fibers characteristic of collagen": "可見具膠原特徵的波浪狀嗜伊紅性纖維",
}
# Legacy English values remain schema-valid in storage and are localized only for display.
DISPLAY_TEXT_TRANSLATIONS.update({
    "Aggregated": "彙整",
    "Amorphous purple material in the background.": "背景可見無定形紫染物質。",
    "Amorphous, non-geometric outer boundary.": "外緣呈無定形且非幾何性輪廓。",
    "Blur": "影像模糊",
    "Blur prevents assessment of spatial relationship to surrounding tissue.": "影像模糊，無法評估與周圍組織的空間關係。",
    "Blur prevents identification of stromal components.": "影像模糊，無法辨識間質成分。",
    "Blur prevents precise measurement of nuclear vs cytoplasmic areas.": "影像模糊，無法精確評估細胞核與細胞質的相對面積。",
    "Blurring": "影像模糊",
    "Blurry edges blending into the background.": "邊緣模糊並與背景融合。",
    "Bone, cartilage, osteoid-like, or mineralized matrix is not visible.": "未見骨、軟骨、類骨質樣或礦化基質。",
    "Cellular cluster": "細胞團塊",
    "Cellular material": "細胞性物質",
    "Cellularity concentrated in one large entity.": "細胞成分集中於單一大型實體。",
    "Cluster": "團塊狀",
    "Dense": "緻密",
    "Diffuse": "瀰漫性",
    "Disorganized": "排列紊亂",
    "Disrupted/Absent": "結構中斷／未見",
    "Eosinophilic": "嗜伊紅性",
    "Eosinophilic-appearing": "呈嗜伊紅性外觀",
    "Epithelial, glandular, ductal, or mucosal structures are not visible.": "未見上皮、腺體、導管或黏膜結構。",
    "Focal": "局灶性",
    "Focal hypercellularity": "局灶性高細胞密度",
    "Focal increase": "局灶性增多",
    "Giant": "巨大",
    "Haphazard": "無規則排列",
    "Hematopoietic-appearing cells": "造血樣細胞",
    "Hemorrhage-appearing": "呈出血樣外觀",
    "Hyperchromatic": "細胞核深染",
    "Hyperchromatic-appearing": "呈細胞核深染外觀",
    "Image quality is not adequate and an appropriate visible reference or internally comparable tissue is not available.": "影像品質不足，且缺乏適當的可見參照或內部可比較組織。",
    "Indeterminate cellular cluster": "性質未能確定的細胞團塊",
    "Inflammatory cell population": "發炎細胞族群",
    "Insufficient cell population for uniformity assessment.": "細胞族群數量不足，無法評估一致性。",
    "Insufficient stromal material for cellularity assessment.": "間質量不足，無法評估間質細胞密度。",
    "Insufficient stromal material for density assessment.": "間質量不足，無法評估間質緻密度。",
    "Insufficient tissue for architectural or population analysis": "組織量不足，無法進行結構或細胞族群分析",
    "Irregular cytoplasmic appearance, but blur prevents confirmation of necrosis.": "細胞質外觀不規則，但影像模糊，無法確認是否為壞死。",
    "Isolated cells": "散在單一細胞",
    "Lack of complex tissue organization.": "缺乏複雜的組織結構。",
    "Large": "大型",
    "Large, purple-stained cellular mass occupying the center of the ROI.": "感興趣區域中央可見一大型紫染細胞性團塊。",
    "Low resolution": "低解析度",
    "Lymphoid or hematopoietic tissue is not visible.": "未見淋巴或造血組織。",
    "Mesenchymal, spindle, adipocytic-appearing, myxoid, fibrous, vascular, nerve-sheath-like, or related soft-tissue components are not visible.": "未見間葉性、梭形細胞、脂肪細胞樣、黏液樣、纖維性、血管性、神經鞘樣或相關軟組織成分。",
    "Mitotic figures": "有絲分裂象",
    "Mixed": "混合型",
    "Moderate to Large": "中等至大型",
    "Nested/Cluster": "巢狀／團塊狀",
    "Neural, glial, neuropil-like, or nerve structures are not visible.": "未見神經、膠質、神經網樣或神經結構。",
    "No adequate reference tissue available within the ROI for comparison.": "感興趣區域內缺乏足夠的參照組織可供比較。",
    "No basophilic crystalline or amorphous mineral deposits visible.": "未見嗜鹼性結晶狀或無定形礦物質沉積。",
    "No bone or cartilage identified.": "未辨識到骨或軟骨組織。",
    "No bone or cartilage present.": "未見骨或軟骨組織。",
    "No clear glandular or ductal organization visible; cells are arranged in solid sheets.": "未見清楚的腺體或導管結構；細胞呈實性片狀排列。",
    "No clear tissue border or interface visible in the high-magnification ROI.": "高倍感興趣區域內未見清楚的組織邊界或介面。",
    "No distinct mesenchymal patterns identified.": "未辨識到明確的間葉性型態。",
    "No lymphoid or hematopoietic tissue identified.": "未辨識到淋巴或造血組織。",
    "No lymphoid or hematopoietic tissue present.": "未見淋巴或造血組織。",
    "No muscle tissue identified.": "未辨識到肌肉組織。",
    "No muscle tissue present.": "未見肌肉組織。",
    "No neural or glial structures identified.": "未辨識到神經或膠質結構。",
    "No neural or glial structures present.": "未見神經或膠質結構。",
    "No organized arrangement visible.": "未見規則排列。",
    "No organized epithelial or glandular structures visible.": "未見具組織性的上皮或腺體結構。",
    "No preserved native architecture visible.": "未見保存的原生組織結構。",
    "No relevant tissue border or interface is visible within the supplied image or ROI.": "所提供影像或感興趣區域內未見可評估的相關組織邊界或介面。",
    "No true lumina or cystic spaces identified.": "未辨識到真正腔隙或囊性空間。",
    "No true or possible lumen, cyst, channel, intracellular space, or tissue space is visible.": "未見真正或疑似腔隙、囊腔、管道、細胞內空間或組織空間。",
    "No visible vessels or erythrocytes.": "未見可辨識血管或紅血球。",
    "Not Evaluable": "無法評估",
    "Nuclear size, shape, and chromatin": "細胞核大小、形狀與染色質",
    "Nuclear-to-cytoplasmic ratio": "核質比",
    "One large, isolated cellular mass.": "可見單一大型且孤立的細胞性團塊。",
    "Only a single ROI provided.": "僅提供單一感興趣區域。",
    "Organ identity is not explicitly supplied and relevant organ-specific parenchyma is not visible.": "未明確提供器官資訊，且未見相關器官特異性實質組織。",
    "Organ identity not supplied.": "未提供器官資訊。",
    "Oval": "卵圓形",
    "Oval to Polygonal": "卵圓形至多角形",
    "Poor": "不佳",
    "Poor focus": "對焦不佳",
    "Predominant population is epithelioid rather than mesenchymal.": "主要細胞族群呈類上皮樣，而非間葉樣。",
    "Presence of necrosis": "壞死是否存在",
    "Purple/pink cytoplasmic hue.": "細胞質呈紫紅至粉紅色色調。",
    "Resolution too low to assess chromatin pattern.": "解析度過低，無法評估染色質型態。",
    "Resolution too low to assess contour.": "解析度過低，無法評估輪廓。",
    "Resolution too low to assess variation.": "解析度過低，無法評估變異程度。",
    "Resolution too low to determine nuclear shape.": "解析度過低，無法判定細胞核形狀。",
    "Resolution too low to distinguish individual nuclei.": "解析度過低，無法分辨個別細胞核。",
    "Resolution too low to identify inflammatory cells.": "解析度過低，無法辨識發炎細胞。",
    "Resolution too low to identify mitotic figures.": "解析度過低，無法辨識有絲分裂象。",
    "Resolution too low to identify nucleoli.": "解析度過低，無法辨識核仁。",
    "Round to Irregular": "圓形至不規則形",
    "Scant": "極少量",
    "Severe image blur and low resolution prevent reliable morphological assessment of nuclear features.": "影像嚴重模糊且解析度低，無法可靠評估細胞核形態特徵。",
    "Significant blur and low resolution prevent detailed nuclear and cytoplasmic assessment.": "影像明顯模糊且解析度低，無法詳細評估細胞核與細胞質。",
    "Single cell present; cohesion cannot be assessed.": "僅見單一細胞，無法評估細胞黏附性。",
    "Single cells": "散在單一細胞",
    "Single large cellular entity centered in the field.": "視野中央可見單一大型細胞性實體。",
    "Single-cell/Isolated": "單細胞／孤立分布",
    "Smooth-muscle-like or skeletal-muscle-like tissue is not visible.": "未見平滑肌樣或骨骼肌樣組織。",
    "Sparse": "稀疏",
    "Sufficient": "充分",
    "The ROI contains a single, giant, irregularly shaped cell with eosinophilic-appearing cytoplasm and indistinct borders. The image is characterized by significant blur and low resolution, which prevents the evaluation of nuclear details, chromatin, or mitotic activity. Amorphous cellular debris is present in the background.": "感興趣區域內可見單一巨大且形狀不規則的細胞；細胞質呈嗜伊紅性外觀，邊界不清。影像明顯模糊且解析度低，無法評估細胞核細節、染色質或有絲分裂活性。背景可見無定形細胞碎屑。",
    "The visible cell is significantly larger than typical mononuclear cells.": "可見細胞明顯大於一般單核細胞。",
    "Variable": "具變異性",
    "Very little visible stroma surrounding the cell.": "細胞周圍僅見極少量間質。",
    "Very small field of view containing only a single cell": "視野範圍極小，僅含單一細胞",
    "WSI coverage, multiple ROIs, or more than one spatially separable region is not supplied.": "未提供全視野切片涵蓋範圍、多個感興趣區域或超過一個可空間區分的區域。",
    "像素化 (Pixelation)": "像素化",
    "實體巢狀 (Solid nests)": "實性巢狀",
    "細胞團塊 (Cell cluster)": "細胞團塊",
    "觀察到明顯的核大小與形狀差異（Pleomorphism）": "觀察到明顯的細胞核大小與形狀差異（多形性）",
})


def schema_label(key: Any) -> str:
    raw = str(key).removesuffix(".md")
    return SCHEMA_FIELD_LABELS.get(
        raw, SKILL_DISPLAY_LABELS.get(raw, raw.replace("_", " "))
    )


def schema_english_label(key: Any) -> str:
    raw = str(key).removesuffix(".md")
    return raw.replace("_", " ")


def bilingual_field_label(key: Any) -> str:
    return (
        '<span class="bilingual-field-title">'
        + html.escape(schema_label(key))
        + '<small>'
        + html.escape(schema_english_label(key))
        + '</small></span>'
    )


def _schema_text(value: Any) -> str:
    """Localize display text without mutating persisted schema data."""
    if isinstance(value, bool):
        return "是" if value else "否"
    if value is None:
        return "—"
    raw = str(value)
    return DISPLAY_TEXT_TRANSLATIONS.get(raw, raw)


def _status_class(status: Any) -> str:
    normalized = str(status).strip().lower().replace("_", " ")
    if normalized in {"present", "available", "adequate", "assessable"}:
        return "is-present"
    if normalized in {"absent", "none", "not present"}:
        return "is-absent"
    if normalized in {"not evaluable", "n/a", "unknown", "unavailable"}:
        return "is-unknown"
    return "is-neutral"


def _observation_nodes(value: Any) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if "Value" in value or "Status" in value:
            nodes.append(value)
        else:
            for child in value.values():
                nodes.extend(_observation_nodes(child))
    elif isinstance(value, list):
        for child in value:
            nodes.extend(_observation_nodes(child))
    return nodes


def _render_list(items: Any, empty: str = "未列出") -> str:
    values = items if isinstance(items, list) else ([] if items in (None, "") else [items])
    if not values:
        return f'<span class="structured-empty">{html.escape(empty)}</span>'
    return "<ul>" + "".join(
        f"<li>{html.escape(_schema_text(item))}</li>" for item in values
    ) + "</ul>"


def _render_observation_card(key: str, value: Any) -> str:
    field_title = bilingual_field_label(key)
    if not isinstance(value, dict) or not ("Value" in value or "Status" in value):
        body = html.escape(json.dumps(value, ensure_ascii=False, indent=2))
        return (
            '<article class="finding-card finding-generic">'
            f"<h4>{field_title}</h4><pre>{body}</pre></article>"
        )
    raw_status = value.get("Status", "N/A")
    status = _schema_text(raw_status)
    raw_observed = value.get("Value", "—")
    if isinstance(raw_observed, list):
        observed = "<ul>" + "".join(
            f"<li>{html.escape(_schema_text(item))}</li>" for item in raw_observed
        ) + "</ul>"
    else:
        observed = html.escape(_schema_text(raw_observed))
    evidence = value.get("Supporting_Visible_Evidence", [])
    return f"""
    <article class="finding-card">
      <div class="finding-card-head"><h4>{field_title}</h4><span class="finding-status {_status_class(raw_status)}">{html.escape(status)}</span></div>
      <div class="finding-value">{observed}</div>
      <div class="finding-evidence"><span>影像可見依據<small>Supporting Visible Evidence</small></span>{_render_list(evidence, '未提供影像可見依據')}</div>
    </article>"""


def _render_finding_group(
    title: str,
    value: Any,
    anchor: str,
    english_title: str = "",
) -> str:
    if not isinstance(value, dict):
        cards = _render_observation_card(title, value)
    else:
        cards = "".join(_render_observation_card(key, item) for key, item in value.items())
    english = html.escape(english_title or schema_english_label(title))
    return f"""
    <section class="structured-section" id="{html.escape(anchor)}">
      <div class="structured-section-title"><span>{html.escape(title)}<small class="section-english-title">{english}</small></span><small>{len(_observation_nodes(value))} 項形態學觀察</small></div>
      <div class="finding-grid">{cards}</div>
    </section>"""

def empty_structured_report(message: str = "完成異常區域分析後，將在此顯示分區報告。") -> str:
    return f"""
    <div class="structured-report structured-report-empty">
      <div class="structured-empty-icon">▦</div>
      <h2>尚無結構化報告</h2>
      <p>{html.escape(message)}</p>
      <div class="structured-empty-steps"><span>1 · YOLO 定位</span><span>2 · 選擇區域</span><span>3 · 分析推論模型</span></div>
    </div>"""


def build_structured_report(
    output: dict[str, Any],
    case_id: str = "",
    model_name: str = "",
    user_edited: bool = False,
    region_label: str = "",
) -> str:
    """Build the bilingual reading view for model or user-revised structured data."""
    if not isinstance(output, dict) or not output:
        return empty_structured_report()

    findings = output.get("Findings", {})
    findings = findings if isinstance(findings, dict) else {}
    metadata = output.get("Analysis_Metadata", {})
    metadata = metadata if isinstance(metadata, dict) else {}
    context = output.get("Image_Context", {})
    context = context if isinstance(context, dict) else {}
    quality = output.get("Image_Quality", {})
    quality = quality if isinstance(quality, dict) else {}
    morphology = output.get("Morphological_Summary", {})
    morphology = morphology if isinstance(morphology, dict) else {}
    limitations = output.get("Limitations", {})
    limitations = limitations if isinstance(limitations, dict) else {}

    nodes = _observation_nodes(findings)
    status_counts = Counter(str(node.get("Status", "N/A")) for node in nodes)
    present = sum(count for status, count in status_counts.items() if _status_class(status) == "is-present")
    absent = sum(count for status, count in status_counts.items() if _status_class(status) == "is-absent")
    unknown = max(0, len(nodes) - present - absent)
    total = max(1, len(nodes))
    summary = _schema_text(morphology.get("Direct_Observations_Only", "尚未提供直接形態觀察。"))

    context_items = [
        ("影像類型", "Image Type", context.get("Image_Type", "—")),
        ("染色", "Stain", context.get("Stain", "—")),
        ("倍率／MPP", "Magnification / MPP", context.get("Magnification_or_MPP", "—")),
        ("器官／部位", "Organ / Site", context.get("Organ_or_Site", "—")),
        ("整體可評估性", "Overall Assessability", quality.get("Overall_Assessability", "—")),
        ("染色品質", "Staining Quality", quality.get("Staining_Quality", "—")),
    ]
    context_html = "".join(
        f'<div><span>{html.escape(label)}<small>{html.escape(english)}</small></span><strong>{html.escape(_schema_text(value))}</strong></div>'
        for label, english, value in context_items
    )

    finding_sections = "".join(
        _render_finding_group(
            SCHEMA_SECTION_LABELS.get(key, schema_label(key)),
            value,
            f"section-{index}",
            schema_english_label(key),
        )
        for index, (key, value) in enumerate(findings.items(), start=1)
        if key != "Conditional_Findings"
    )

    conditional = findings.get("Conditional_Findings", {})
    conditional_sections = ""
    if isinstance(conditional, dict):
        module_findings = conditional.get("Module_Findings", {})
        if isinstance(module_findings, dict) and module_findings:
            conditional_sections = "".join(
                _render_finding_group(
                    f"條件模組 · {schema_label(module_name)}",
                    module_value,
                    f"module-{index}",
                    f"Conditional Module · {schema_english_label(module_name)}",
                )
                for index, (module_name, module_value) in enumerate(module_findings.items(), start=1)
            )

    quality_details = "".join(
        f'<div class="detail-row"><span>{bilingual_field_label(key)}</span><strong>{html.escape(_schema_text(value))}</strong></div>'
        if not isinstance(value, list)
        else f'<div class="detail-row detail-list"><span>{bilingual_field_label(key)}</span>{_render_list(value)}</div>'
        for key, value in quality.items()
    )
    limit_blocks = "".join(
        f'<div class="limit-block"><span>{bilingual_field_label(key)}</span>{_render_list(value)}</div>'
        for key, value in limitations.items()
        if key not in {"Human_Review_Suggested", "Human_Review_Reason"}
    )
    review_needed = bool(limitations.get("Human_Review_Suggested", False))
    review_class = "review-required" if review_needed else "review-advised"
    provenance = (
        "使用者修訂版 · 保留原結構"
        if user_edited
        else f"經結構規範驗證 · 版本 {_schema_text(output.get('Schema_Version', '—'))}"
    )

    return f"""
    <div class="structured-report">
      <header class="structured-hero">
        <div><div class="structured-kicker">{html.escape(provenance)}</div><h2>病理形態結構化報告</h2><p>{html.escape(_schema_text(metadata.get('Analysis_Purpose', '非診斷性病理影像形態輔助分析')))}</p></div>
        <div class="structured-identity"><span>{'分析範圍' if region_label else '個案'}</span><strong>{html.escape(region_label or case_id or '目前分析')}</strong><small>{html.escape(('個案 ' + case_id + ' · ' if region_label and case_id else '') + (model_name or '結構化分析模型'))}</small></div>
      </header>
      <div class="structured-stat-grid">
        <div class="structured-stat"><span>形態學觀察項目</span><strong>{len(nodes)}</strong><small>Morphological Findings</small></div>
        <div class="structured-stat stat-present"><span>可見／存在</span><strong>{present}</strong><small>Visible / Present</small></div>
        <div class="structured-stat stat-absent"><span>未見／不存在</span><strong>{absent}</strong><small>Absent / Not Identified</small></div>
        <div class="structured-stat stat-unknown"><span>未知／不可評估</span><strong>{unknown}</strong><small>Unknown / Not Evaluable</small></div>
      </div>
      <section class="status-visual"><div class="status-visual-title"><strong>形態所見狀態分布<small class="inline-english-title">Morphological Finding Status Distribution</small></strong><span>{len(nodes)} 項形態學觀察</span></div><div class="status-track"><i class="bar-present" style="width:{present / total:.2%}"></i><i class="bar-absent" style="width:{absent / total:.2%}"></i><i class="bar-unknown" style="width:{unknown / total:.2%}"></i></div><div class="status-legend"><span><i class="dot-present"></i>可見 {present}</span><span><i class="dot-absent"></i>未見 {absent}</span><span><i class="dot-unknown"></i>不可評估 {unknown}</span></div></section>
      <section class="structured-summary"><div class="structured-section-title"><span>整合形態學摘要<small class="section-english-title">Integrated Morphological Summary</small></span><small>僅依據影像直接觀察</small></div><p>{html.escape(summary)}</p><div class="summary-caveats"><div><strong>不確定所見<small>Uncertain Findings</small></strong>{_render_list(morphology.get('Uncertain_Findings', []), '無')}</div><div><strong>無法評估<small>Not Evaluable Findings</small></strong>{_render_list(morphology.get('Not_Evaluable_Findings', []), '無')}</div></div></section>
      <section class="structured-context"><div class="structured-section-title"><span>影像與檢體脈絡／可判讀性<small class="section-english-title">Image and Specimen Context / Assessability</small></span><small>影像與檢體資訊</small></div><div class="context-grid">{context_html}</div><div class="quality-detail-grid">{quality_details}</div></section>
      {finding_sections}
      {conditional_sections}
      <section class="structured-section limitation-section"><div class="structured-section-title"><span>分析限制與人工複核<small class="section-english-title">Limitations and Human Review</small></span><small>可判讀性與取樣限制</small></div><div class="limit-grid">{limit_blocks or '<span class="structured-empty">未列出限制</span>'}</div><div class="review-banner {review_class}"><strong>{'建議人工複核' if review_needed else '模型未標記強制複核'}</strong><span>{html.escape(_schema_text(limitations.get('Human_Review_Reason', 'AI 結果仍應由專業人員確認。')))}</span></div></section>
      <footer class="structured-disclaimer">本頁為非診斷性影像形態輔助整理；不得取代病理專業判讀、臨床資訊整合或正式診斷。</footer>
    </div>"""


def _report_detection_key(report: dict[str, Any]) -> str:
    value = report.get("detection_index")
    if value is None:
        value = report.get("region_index", 1)
    return str(value)


def _report_model_key(report: dict[str, Any]) -> str:
    return str(report.get("model_key", "")).strip()


def _region_report_key(report: dict[str, Any]) -> str:
    detection_key = _report_detection_key(report)
    model_key = _report_model_key(report)
    return f"{model_key}::{detection_key}" if model_key else detection_key


def structured_region_reports(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    """Return canonical model × ROI reports, with a legacy fallback."""
    analysis = metadata.get("analysis", {}) if isinstance(metadata, dict) else {}
    vlm = analysis.get("student_vlm", {}) if isinstance(analysis, dict) else {}
    fallback_model_key = (
        str(vlm.get("model_key", "")).strip() if isinstance(vlm, dict) else ""
    ) or "legacy"
    reports = vlm.get("region_reports", []) if isinstance(vlm, dict) else []
    if isinstance(reports, list) and reports:
        canonical: list[dict[str, Any]] = []
        for raw_report in reports:
            if not isinstance(raw_report, dict):
                continue
            report = dict(raw_report)
            report["model_key"] = (
                str(report.get("model_key", "")).strip() or fallback_model_key
            )
            for field in ("model_name", "model_id"):
                if not report.get(field) and isinstance(vlm, dict) and vlm.get(field):
                    report[field] = vlm[field]
            canonical.append(report)
        return canonical
    output = vlm.get("structured_output") if isinstance(vlm, dict) else None
    if not isinstance(output, dict) or not output:
        return []
    regions = analysis.get("vlm_regions", []) if isinstance(analysis, dict) else []
    first_region = regions[0] if isinstance(regions, list) and regions else {}
    return [{
        "region_index": first_region.get("region_index", 1),
        "detection_index": first_region.get("detection_index", 1),
        "status": vlm.get("status", "completed"),
        "model_key": fallback_model_key,
        "model_name": vlm.get("model_name", ""),
        "model_id": vlm.get("model_id", ""),
        "structured_output": output,
        "user_edited": vlm.get("user_edited", False),
    }]


def _select_region_report(
    reports: list[dict[str, Any]], preferred_key: str | int | None = None
) -> dict[str, Any] | None:
    preferred = str(preferred_key) if preferred_key is not None else ""
    if preferred:
        selected = next(
            (report for report in reports if _region_report_key(report) == preferred),
            None,
        )
        if selected is None and "::" not in preferred:
            selected = next(
                (
                    report for report in reports
                    if _report_detection_key(report) == preferred
                ),
                None,
            )
        if selected is not None:
            return selected
    return next(
        (
            report for report in reports
            if report.get("status") == "completed"
            and isinstance(report.get("structured_output"), dict)
        ),
        reports[0] if reports else None,
    )


def _model_report_choices(
    reports: list[dict[str, Any]],
) -> list[tuple[str, str]]:
    choices: list[tuple[str, str]] = []
    seen: set[str] = set()
    for report in reports:
        model_key = _report_model_key(report)
        if not model_key or model_key in seen:
            continue
        seen.add(model_key)
        model_name = str(
            report.get("model_name") or model_key or "分析推論模型"
        ).strip()
        choices.append((model_name, model_key))
    return choices


def model_report_selector_update(
    reports: list[dict[str, Any]], selected_key: str | int | None = None
) -> Any:
    choices = _model_report_choices(reports)
    selected = _select_region_report(reports, selected_key)
    value = _report_model_key(selected) if selected is not None else None
    return gr.update(
        choices=choices,
        value=value,
        visible=bool(choices),
        interactive=len(choices) > 1,
    )


def region_report_selector_update(
    reports: list[dict[str, Any]], selected_key: str | int | None = None
) -> Any:
    selected = _select_region_report(reports, selected_key)
    model_key = _report_model_key(selected) if selected is not None else ""
    scoped_reports = [
        report for report in reports if _report_model_key(report) == model_key
    ]
    choices = [
        (f"異常區域 {_report_detection_key(report)}", _region_report_key(report))
        for report in scoped_reports
    ]
    value = _region_report_key(selected) if selected is not None else None
    return gr.update(
        choices=choices,
        value=value,
        visible=bool(choices),
        interactive=len(choices) > 1,
    )


def render_region_report(report: dict[str, Any] | None, case_id: str) -> str:
    if not report:
        return empty_structured_report()
    detection_key = _report_detection_key(report)
    output = report.get("structured_output", {})
    if report.get("status") != "completed" or not isinstance(output, dict) or not output:
        error = str(report.get("error", report.get("status", "分析未完成")))
        return empty_structured_report(
            f"異常區域 {detection_key} 的結構化分析未完成：{error}"
        )
    return build_structured_report(
        output,
        case_id,
        str(report.get("model_name", "")),
        user_edited=bool(report.get("user_edited")),
        region_label=f"異常區域 {detection_key}",
    )


def structured_region_view(
    metadata: dict[str, Any], preferred_key: str | int | None = None
) -> tuple[list[dict[str, Any]], dict[str, Any], str | None, str, Any, Any]:
    reports = structured_region_reports(metadata)
    selected = _select_region_report(reports, preferred_key)
    output = selected.get("structured_output", {}) if selected else {}
    if not isinstance(output, dict):
        output = {}
    selected_key = _region_report_key(selected) if selected else None
    return (
        reports,
        output,
        selected_key,
        render_region_report(selected, str(metadata.get("case_id", ""))),
        model_report_selector_update(reports, selected_key),
        region_report_selector_update(reports, selected_key),
    )


def switch_structured_report_model(
    reports: list[dict[str, Any]] | None,
    selected_model_key: str | None,
    active_report_key: str | None,
    case_id: str,
) -> tuple[dict[str, Any], str, str | None, Any]:
    available = reports or []
    current_detection = (
        str(active_report_key).rsplit("::", 1)[-1] if active_report_key else ""
    )
    preferred_key = (
        f"{selected_model_key}::{current_detection}"
        if selected_model_key and current_detection
        else ""
    )
    selected = _select_region_report(available, preferred_key)
    if selected_model_key and (
        selected is None or _report_model_key(selected) != selected_model_key
    ):
        selected = next(
            (
                report for report in available
                if _report_model_key(report) == selected_model_key
            ),
            None,
        )
    output = selected.get("structured_output", {}) if selected else {}
    if not isinstance(output, dict):
        output = {}
    key = _region_report_key(selected) if selected else None
    return (
        output,
        render_region_report(selected, case_id),
        key,
        region_report_selector_update(available, key),
    )


def switch_structured_region_report(
    reports: list[dict[str, Any]] | None,
    selected_key: str | None,
    case_id: str,
) -> tuple[dict[str, Any], str, str | None]:
    selected = _select_region_report(reports or [], selected_key)
    output = selected.get("structured_output", {}) if selected else {}
    if not isinstance(output, dict):
        output = {}
    key = _region_report_key(selected) if selected else None
    return (
        output,
        render_region_report(selected, case_id),
        key,
    )

def build_schema_output(output: dict[str, Any]) -> str:
    """Render validated Output_Schema JSON into readable, escaped sections."""
    if not isinstance(output, dict) or not output:
        return "<p>尚無結構化分析輸出。</p>"
    sections: list[str] = []
    findings = output.get("Findings", {})
    ordered = [
        ("Analysis_Metadata", output.get("Analysis_Metadata", {})),
        ("Image_Context", output.get("Image_Context", {})),
        ("Image_Quality", output.get("Image_Quality", {})),
    ]
    if isinstance(findings, dict):
        ordered.extend((key, value) for key, value in findings.items())
    ordered.extend(
        [
            ("Morphological_Summary", output.get("Morphological_Summary", {})),
            ("Limitations", output.get("Limitations", {})),
        ]
    )
    for key, value in ordered:
        label = SCHEMA_SECTION_LABELS.get(key, key)
        body = html.escape(json.dumps(value, ensure_ascii=False, indent=2))
        sections.append(
            f'<section class="schema-section"><h4>{html.escape(label)}</h4><pre>{body}</pre></section>'
        )
    return "".join(sections)


def schema_report_text(output: dict[str, Any]) -> str:
    """Map the best-prompt schema's summary and limitations to report text."""
    morphology = output.get("Morphological_Summary", {}) if isinstance(output, dict) else {}
    limitations = output.get("Limitations", {}) if isinstance(output, dict) else {}
    direct = str(morphology.get("Direct_Observations_Only", "")).strip()
    uncertain = morphology.get("Uncertain_Findings", [])
    not_evaluable = morphology.get("Not_Evaluable_Findings", [])
    parts = [f"直接形態觀察：{direct or 'N/A'}"]
    if uncertain:
        parts.append("不確定所見：" + "；".join(str(item) for item in uncertain))
    if not_evaluable:
        parts.append("無法評估：" + "；".join(str(item) for item in not_evaluable))
    limitation_items = []
    for key in ("Image_Limitations", "ROI_Limitations", "Sampling_Limitations"):
        value = limitations.get(key, [])
        if isinstance(value, list):
            limitation_items.extend(str(item) for item in value)
    if limitation_items:
        parts.append("分析限制：" + "；".join(limitation_items))
    if limitations.get("Human_Review_Suggested"):
        parts.append(
            "人工複核：" + str(limitations.get("Human_Review_Reason", "建議專業人員複核"))
        )
    return "\n".join(parts)




def empty_workspace() -> str:
    return """
    <div class="analysis-summary-shell">
      <section class="report-card report-empty analysis-summary-card">
        <div class="report-kicker">AI IMAGE ANALYSIS SUMMARY</div>
        <h3>影像分析摘要</h3>
        <p>完成 YOLO 定位或所選區域的結構化分析後，摘要將顯示於此。</p>
      </section>
    </div>"""


def build_workspace(image: Image.Image, metadata: dict[str, Any], rows: list[list[Any]]) -> str:
    del image
    analysis = metadata.get("analysis", {})
    vlm = analysis.get("student_vlm", {})
    vlm_status = _schema_text(vlm.get("status", "not_requested"))
    vlm_name = str(vlm.get("model_name", "未使用"))
    vlm_summary = _schema_text(str(vlm.get("summary", "")).strip())
    vlm_section = (
        f'<div class="report-section vlm-section"><span>結構化模型直接形態觀察</span><small>Direct Morphological Observation</small><p>{html.escape(vlm_summary)}</p></div>'
        if vlm_summary
        else ""
    )
    case_id = html.escape(str(metadata.get("case_id", "")))
    created_at = html.escape(str(metadata.get("created_at", "")))
    summary = html.escape(_schema_text(str(analysis.get("ai_assessment", ""))))
    distribution = Counter(str(row[1]).replace("_", " ") for row in rows)
    distribution_text = "、".join(
        f"{html.escape(label)} × {count}" for label, count in distribution.most_common()
    ) or "未偵測到候選異常區域"
    return f"""
    <div class="analysis-summary-shell">
      <section class="report-card analysis-summary-card">
        <div class="report-header"><div><div class="report-kicker">AI IMAGE ANALYSIS SUMMARY</div><h3>影像分析摘要</h3></div><span class="report-badge badge-review">待人工複核</span></div>
        <dl class="report-meta"><div><dt>個案編號</dt><dd>{case_id}</dd></div><div><dt>分析時間</dt><dd>{created_at}</dd></div><div><dt>YOLO 模型</dt><dd>{html.escape(str(metadata.get('model',{}).get('name','')))}</dd></div><div><dt>結構化分析模型</dt><dd>{html.escape(vlm_name)} · {html.escape(vlm_status)}</dd></div></dl>
        <div class="report-section"><span>整合摘要</span><small>Integrated Summary</small><p>{summary}</p></div>
        {vlm_section}
        <div class="report-section"><span>候選類別分布</span><small>Candidate Class Distribution</small><p>{distribution_text}</p></div>
        <div class="report-alert">AI 結果僅供研究與輔助判讀，不可取代病理專業判讀與正式醫療診斷。</div>
      </section>
    </div>"""

def history_rows(items: list[dict[str, Any]]) -> list[list[Any]]:
    return [
        [
            item.get("case_id", ""),
            item.get("created_at", ""),
            item.get("image_size", ""),
            item.get("candidate_count", 0),
            item.get("top_label", ""),
            f"{float(item['max_confidence']):.1%}" if item.get("max_confidence") is not None else "—",
            item.get("status", ""),
            item.get("localization_model", ""),
            item.get("student_model", ""),
            item.get("student_vlm_status", "not_requested"),
        ]
        for item in items
    ]


def detection_region_choices(metadata: dict[str, Any]) -> list[tuple[str, str]]:
    choices: list[tuple[str, str]] = []
    for item in metadata.get("detections", []):
        index = int(item.get("index", len(choices) + 1))
        label = str(item.get("class_name", "unknown"))
        confidence = float(item.get("confidence", 0.0))
        box = item.get("bbox_xyxy", [0, 0, 0, 0])
        box_text = ", ".join(f"{float(value):.0f}" for value in box[:4])
        choices.append(
            (f"區域 {index} · {label} · {confidence:.1%} · [{box_text}]", str(index))
        )
    return choices


def selected_detection_values(metadata: dict[str, Any]) -> list[str]:
    return [
        str(region.get("detection_index"))
        for region in metadata.get("analysis", {}).get("vlm_regions", [])
        if region.get("detection_index") is not None
    ]


def analyze_remote(
    token: str,
    input_image: Image.Image | None,
    confidence: float,
    iou: float,
    max_detections: int,
    localization_model: str | None,
    student_model: str | None,
) -> tuple[Any, ...]:
    """Stage 1: localize only; the analysis model waits for user-selected ROIs."""
    if input_image is None:
        raise gr.Error("請先上傳影像。")
    if not localization_model:
        raise gr.Error("請選擇異常定位模型。")
    api = api_for(token)
    try:
        normalized = ImageOps.exif_transpose(input_image).convert("RGB")
        metadata = api.analyze(
            normalized,
            confidence,
            iou,
            int(max_detections),
            student_model=YOLO_ONLY_STUDENT_MODEL,
            localization_model=localization_model,
        )
        original = api.get_image(metadata["artifacts"]["original"])
        rows = rows_from_metadata(metadata)
        items = api.list_analyses()
    except APIClientError as exc:
        raise gr.Error(str(exc)) from exc

    case_id = str(metadata["case_id"])
    summary = str(metadata.get("analysis", {}).get("ai_assessment", ""))
    choices = detection_region_choices(metadata)
    if rows:
        upload_status = (
            f"**YOLO 定位完成** · `{localization_model}` 找到 **{len(rows)}** 個候選區域。"
            "請勾選一個或多個區域，再執行結構化分析。"
        )
        analysis_status = (
            f"### 第 1 階段完成\n個案 `{case_id}` 已儲存；分析推論模型尚未執行。"
        )
    else:
        upload_status = (
            f"**YOLO 定位完成** · `{localization_model}` 未偵測到候選異常區域；"
            "依設定不會執行結構化分析。"
        )
        analysis_status = (
            f"### 未偵測到異常區域\n個案 `{case_id}` 已儲存，分析推論模型未啟動。"
        )
    return (
        upload_status,
        analysis_status,
        build_metrics(metadata),
        build_workspace(original, metadata, rows),
        {},
        empty_structured_report("請先選擇偵測到的異常區域，再執行結構化分析。"),
        [],
        gr.update(choices=[], value=None, visible=False, interactive=False),
        gr.update(choices=[], value=None, visible=False, interactive=False),
        None,
        history_rows(items),
        case_id,
        case_id,
        summary,
        metadata.get("report", {}).get("report_status", "草稿"),
        gr.update(choices=choices, value=[], interactive=bool(choices)),
        gr.update(interactive=False),
        list(metadata.get("detections", [])),
    )


def analyze_selected_regions(
    token: str,
    case_id: str,
    student_model: str | None,
    selected_regions: list[str] | None,
) -> tuple[Any, ...]:
    """Analyze every selected YOLO region independently, with backend parallelism."""
    if not case_id:
        raise gr.Error("請先完成 YOLO 異常定位。")
    if not student_model:
        raise gr.Error("請選擇 Mistral Small 3.1 或 Gemma 4。")
    values = list(dict.fromkeys(selected_regions or []))
    if not values:
        raise gr.Error("請至少勾選一個異常區域。")
    if len(values) > MAX_SELECTED_VLM_REGIONS:
        raise gr.Error(f"每次最多可分析 {MAX_SELECTED_VLM_REGIONS} 個區域。")
    try:
        indices = [int(value) for value in values]
    except (TypeError, ValueError) as exc:
        raise gr.Error("異常區域選擇值無效，請重新執行 YOLO 定位。") from exc
    api = api_for(token)
    try:
        metadata = api.analyze_regions(case_id, student_model, indices)
        original = api.get_image(metadata["artifacts"]["original"])
        items = api.list_analyses()
    except APIClientError as exc:
        raise gr.Error(str(exc)) from exc
    rows = rows_from_metadata(metadata)
    analysis = metadata.get("analysis", {})
    vlm = analysis.get("student_vlm", {})
    status = str(vlm.get("last_run_status", vlm.get("status", "failed")))
    model_name = str(vlm.get("model_name", student_model))
    error = str(vlm.get("last_run_error", vlm.get("error", ""))).strip()
    preferred_report_key = f"{student_model}::{indices[0]}"
    (
        reports,
        structured_output,
        active_index,
        report_html,
        model_selector_update,
        region_selector_update,
    ) = structured_region_view(metadata, preferred_report_key)
    selected_index_set = {str(index) for index in indices}
    current_reports = [
        report for report in reports
        if _report_model_key(report) == student_model
        and _report_detection_key(report) in selected_index_set
    ]
    if not current_reports and reports:
        selected_report = _select_region_report(reports, preferred_report_key)
        current_reports = [selected_report] if selected_report else []
    completed_count = sum(
        report.get("status") == "completed"
        and isinstance(report.get("structured_output"), dict)
        for report in current_reports
    )
    total_saved = sum(
        report.get("status") == "completed"
        and isinstance(report.get("structured_output"), dict)
        for report in reports
    )
    if status == "completed":
        upload_status = (
            f"**結構化分析完成** · {model_name} 已新增或更新 **{completed_count}** 份報告；"
            f"本個案目前共保存 **{total_saved}** 份模型 × 異常區域報告。"
        )
        analysis_status = f"### YOLO＋分析推論模型完成\n個案 {case_id} 已更新。"
    elif status == "partial":
        upload_status = (
            f"**結構化分析部分完成** · 已完成 **{completed_count}/{len(current_reports)}** 份"
            f"本次報告；{error or '其餘區域可稍後重試'}。"
        )
        analysis_status = f"### 部分區域分析完成\n個案 {case_id} 已保存所有成功報告。"
    else:
        upload_status = f"**結構化分析失敗** · {model_name}：{error or status}"
        analysis_status = f"### 分析推論模型未完成\n個案 {case_id} 已保留既有報告，可修正後重試。"
    return (
        upload_status,
        analysis_status,
        build_metrics(metadata),
        build_workspace(original, metadata, rows),
        structured_output,
        report_html,
        reports,
        model_selector_update,
        region_selector_update,
        active_index,
        history_rows(items),
        str(analysis.get("ai_assessment", "")),
        metadata.get("report", {}).get("report_status", "草稿"),
    )


def refresh_history(token: str) -> tuple[list[list[Any]], str]:
    try:
        items = api_for(token).list_analyses()
    except APIClientError as exc:
        raise gr.Error(str(exc)) from exc
    return history_rows(items), f"目前共有 **{len(items)}** 筆個案。"


def load_history_case(token: str, case_id: str) -> tuple[Any, ...]:
    """Load an exact backend case selected through the explicit context menu."""
    case_id = (case_id or "").strip()
    if not case_id:
        raise gr.Error("無法辨識右鍵選取的個案紀錄。")
    api = api_for(token)
    try:
        metadata = api.get_analysis(case_id)
        original = api.get_image(metadata["artifacts"]["original"])
    except APIClientError as exc:
        raise gr.Error(str(exc)) from exc
    rows = rows_from_metadata(metadata)
    (
        reports,
        vlm_output,
        active_index,
        report_html,
        model_selector_update,
        region_selector_update,
    ) = structured_region_view(metadata)
    report = metadata.get("report", {})
    region_choices = detection_region_choices(metadata)
    selected_values = selected_detection_values(metadata)
    return (
        draw_selected_regions(original, metadata.get("detections", []), selected_values),
        f"已載入個案 `{case_id}`；右鍵選擇編輯時可修改下方所有欄位。",
        f"### 已載入歷史分析\n`{case_id}`",
        build_metrics(metadata),
        build_workspace(original, metadata, rows),
        vlm_output,
        report_html,
        reports,
        model_selector_update,
        region_selector_update,
        active_index,
        case_id,
        case_id,
        report.get("patient_id", ""),
        report.get("specimen_id", ""),
        report.get("collection_date", ""),
        report.get("specimen_type", ""),
        report.get("anatomical_site", ""),
        report.get("stain", ""),
        report.get("microscopic_findings", ""),
        report.get("ai_summary", metadata.get("analysis", {}).get("ai_assessment", "")),
        report.get("final_diagnosis", ""),
        report.get("notes", ""),
        report.get("reviewer", ""),
        report.get("report_status", "草稿"),
        report.get("signed_at", ""),
        gr.update(
            choices=region_choices,
            value=selected_values,
            interactive=bool(region_choices),
        ),
        gr.update(interactive=bool(selected_values)),
        original,
        list(metadata.get("detections", [])),
    )


def report_payload(
    patient_id: str,
    specimen_id: str,
    collection_date: str,
    specimen_type: str,
    anatomical_site: str,
    stain: str,
    microscopic_findings: str,
    ai_summary: str,
    final_diagnosis: str,
    notes: str,
    reviewer: str,
    report_status: str,
    signed_at: str,
) -> dict[str, str]:
    return {
        "patient_id": patient_id or "",
        "specimen_id": specimen_id or "",
        "collection_date": collection_date or "",
        "specimen_type": specimen_type or "",
        "anatomical_site": anatomical_site or "",
        "stain": stain or "",
        "microscopic_findings": microscopic_findings or "",
        "ai_summary": ai_summary or "",
        "final_diagnosis": final_diagnosis or "",
        "notes": notes or "",
        "reviewer": reviewer or "",
        "report_status": report_status or "草稿",
        "signed_at": signed_at or "",
    }


def create_case_record(token: str, requested_case_id: str) -> tuple[Any, ...]:
    api = api_for(token)
    try:
        metadata = api.create_case((requested_case_id or "").strip())
        items = api.list_analyses()
    except APIClientError as exc:
        raise gr.Error(str(exc)) from exc
    case_id = str(metadata["case_id"])
    return (
        history_rows(items),
        f"已在 Server 新增並載入空白個案 `{case_id}`。",
        "",
        case_id,
        case_id,
        None,
        "請上傳待分析影像，或直接填寫個案內容。",
        "### 新增空白個案",
        empty_metrics_html(),
        empty_workspace(),
        {},
        empty_structured_report("此空白個案尚未進行結構化分析。"),
        [],
        gr.update(choices=[], value=None, visible=False, interactive=False),
        gr.update(choices=[], value=None, visible=False, interactive=False),
        None,
        "", "", "", None, "", None, "", "", "", "", "", "草稿", "",
        gr.update(choices=[], value=[], interactive=False),
        gr.update(interactive=False),
        None,
        [],
    )

def save_case_record(
    token: str,
    original_case_id: str,
    edited_case_id: str,
    patient_id: str,
    specimen_id: str,
    collection_date: str,
    specimen_type: str,
    anatomical_site: str,
    stain: str,
    microscopic_findings: str,
    ai_summary: str,
    final_diagnosis: str,
    notes: str,
    reviewer: str,
    report_status: str,
    signed_at: str,
    active_report_detection_index: str | int | None,
) -> tuple[Any, ...]:
    if not original_case_id:
        raise gr.Error("請先從 Server 紀錄載入一筆個案。")
    if not (edited_case_id or "").strip():
        raise gr.Error("個案編號不可為空白。")
    api = api_for(token)
    payload = report_payload(
        patient_id, specimen_id, collection_date, specimen_type, anatomical_site,
        stain, microscopic_findings, ai_summary, final_diagnosis, notes, reviewer,
        report_status, signed_at,
    )
    try:
        metadata = api.update_case(
            original_case_id,
            edited_case_id.strip(),
            payload,
        )
        items = api.list_analyses()
        original = api.get_image(metadata["artifacts"]["original"])
    except APIClientError as exc:
        raise gr.Error(str(exc)) from exc
    current_id = str(metadata["case_id"])
    rows = rows_from_metadata(metadata)
    vlm = metadata.get("analysis", {}).get("student_vlm", {})
    (
        _reports,
        _structured_output,
        _active_index,
        rendered_report,
        _model_selector,
        _region_selector,
    ) = structured_region_view(metadata, active_report_detection_index)
    return (
        current_id,
        current_id,
        history_rows(items),
        f"個案 `{current_id}` 已更新。",
        build_workspace(original, metadata, rows),
        rendered_report,
    )


def delete_case_record(token: str, case_id: str) -> tuple[Any, ...]:
    if not case_id:
        raise gr.Error("請先從 Server 紀錄載入要刪除的個案。")
    api = api_for(token)
    try:
        api.delete_analysis(case_id)
        items = api.list_analyses()
    except APIClientError as exc:
        raise gr.Error(str(exc)) from exc
    return (
        history_rows(items),
        f"個案 `{case_id}` 已刪除。",
        "",
        "",
        None,
        "尚未載入個案。",
        "### 尚未載入個案",
        empty_metrics_html(),
        empty_workspace(),
        {},
        empty_structured_report("目前未載入個案。"),
        [],
        gr.update(choices=[], value=None, visible=False, interactive=False),
        gr.update(choices=[], value=None, visible=False, interactive=False),
        None,
        "", "", "", None, "", None, "", "", "", "", "", "草稿", "",
        gr.update(choices=[], value=[], interactive=False),
        gr.update(interactive=False),
        None,
        [],
    )


def create_ui() -> gr.Blocks:
    css = CSS_PATH.read_text(encoding="utf-8") + CLIENT_CSS
    with gr.Blocks(
        title="PathoVision REST Client", css=css, js=CLIENT_JS, theme=gr.themes.Soft()
    ) as demo:
        connection_token = gr.State("")
        ssh_session_token = gr.State("")
        active_case_id = gr.State("")
        session_banner = gr.State("")
        vlm_json = gr.State({})
        vlm_region_reports = gr.State([])
        active_report_detection_index = gr.State(None)
        source_image_state = gr.State(None)
        active_detections = gr.State([])
        model_refresh_timer = gr.Timer(value=2.0, active=True)

        with gr.Column(visible=True, elem_id="connection-shell") as connection_panel:
            with gr.Column(elem_classes=["connection-card"]):
                gr.HTML("""
                <div class="connection-hero"><small>PATHOVISION · RESTFUL CLIENT–SERVER</small><h1>NCHC NANO4 使用者登入</h1><p>UI 僅在使用者電腦執行；YOLO、GPU 推論與個案資料位於 NCHC NANO4。</p></div>
                """)
                with gr.Column():
                    with gr.Column():
                        with gr.Row():
                            nchc_host = gr.Textbox(value=DEFAULT_NCHC_HOST, label="NANO4 公開登入主機", interactive=False)
                            nchc_port = gr.Number(value=22, precision=0, label="SSH Port", interactive=False)
                        with gr.Row():
                            username = gr.Textbox(label="NCHC 帳號")
                            password = gr.Textbox(label="NCHC 密碼", type="password")
                        method = gr.Radio(list(NCHC_2FA_METHODS), value=list(NCHC_2FA_METHODS)[0], label="2FA")
                        otp = gr.Textbox(label="Mobile APP OTP", type="password")
                        ssh_button = gr.Button("1. 原生 OpenSSH 登入並掃描專案", variant="primary")
                        project = gr.Dropdown(label="NANO4 專案目錄", choices=[])
                        with gr.Row():
                            partition = gr.Dropdown(
                                choices=["dev", "8gpus"],
                                value="8gpus",
                                allow_custom_value=False,
                                label="Partition",
                            )
                            account = gr.Dropdown([], allow_custom_value=True, label="Account（可留空）")
                            walltime = gr.Textbox(value="04:00:00", label="Walltime")
                        with gr.Row():
                            cpus = gr.Slider(8, 36, value=32, step=4, label="CPU")
                            memory = gr.Slider(64, 512, value=256, step=16, label="Memory GB")
                            gpus = gr.Number(value=3, precision=0, label="GPU", interactive=False)
                            local_port = gr.State(8765)
                        with gr.Row():
                            submit_button = gr.Button("2. 提交 Slurm 並自動連線", variant="primary")
                        managed_status = gr.Markdown("尚未登入 NANO4。", elem_classes=["status-panel"])

        with gr.Column(visible=False) as app_panel:
            gr.HTML("""
            <header class="pv-topbar pv-analysis-header"><div class="pv-analysis-title"><h1>PATHOVISION Analysis System</h1><p>病理醫療影像異常定位之結構化分析輔助系統</p></div></header>
            """)
            with gr.Row(elem_id="api-session-toolbar"):
                disconnect_button = gr.Button(
                    "結束工作階段並歸還資源",
                    variant="stop",
                    size="lg",
                    elem_id="disconnect-resource-button",
                )

            with gr.Tabs(elem_id="workflow-tabs"):
                with gr.Tab("01　圖片定位與分析"):
                    with gr.Row(equal_height=True):
                        with gr.Column(scale=7, elem_classes=["workspace-card"]):
                            input_image = gr.Image(
                                label="上傳待分析影像",
                                type="pil",
                                sources=["upload"],
                                height=520,
                                elem_id="upload-image",
                            )
                        with gr.Column(scale=4, elem_classes=["workspace-card"]):
                            model_loading_view = gr.HTML(
                                model_loading_status_html(),
                                elem_id="model-loading-progress",
                            )
                            localization_model = gr.Dropdown(
                                choices=[],
                                value=None,
                                interactive=False,
                                label="區域異常定位模型",
                                info="YOLO11s 推論快且較準確；YOLO11m 推論稍慢且最準確。",
                            )
                            confidence = gr.Slider(0.05, 0.95, value=0.25, step=0.05, label="Confidence")
                            iou = gr.Slider(0.10, 0.90, value=0.45, step=0.05, label="IoU")
                            max_detections = gr.Slider(1, 300, value=100, step=1, label="最大候選區域數")
                            analyze_button = gr.Button(
                                "1. 執行異常區域定位",
                                variant="primary",
                                interactive=False,
                            )
                            selected_regions = gr.CheckboxGroup(
                                choices=[],
                                value=[],
                                interactive=False,
                                label="選擇要進行結構化分析的異常區域",
                                info=f"可自行多選；每次最多 {MAX_SELECTED_VLM_REGIONS} 個區域。勾選後左側影像會標示目前選取位置。",
                            )
                            student_model = gr.Dropdown(
                                choices=[],
                                value=None,
                                interactive=False,
                                label="分析推論模型",
                                info="Mistral 推論較快；Gemma4 理解與推理能力最佳。模型就緒後會自動顯示。",
                            )
                            with gr.Row():
                                student_model_refresh = gr.Button(
                                    "立即重新檢查模型",
                                    size="sm",
                                    interactive=False,
                                )
                                student_analysis_button = gr.Button(
                                    "2. 分析所選異常區域",
                                    variant="primary",
                                    interactive=False,
                                )
                            upload_status = gr.Markdown(
                                "影像只會透過本機 SSH Tunnel 傳送至 NANO4 Server。",
                                elem_classes=["status-panel"],
                            )
                    metrics = gr.HTML(empty_metrics_html())
                    analysis_status = gr.Markdown("尚未分析。", elem_classes=["status-panel"])
                    workspace = gr.HTML(empty_workspace())

                with gr.Tab("02　結構化視覺報告"):
                    with gr.Row(elem_classes=["structured-page-toolbar"]):
                        gr.HTML('<div class="section-heading structured-page-heading"><div class="kicker">病理形態結構化報告</div><h2>模型輸出統整與視覺化</h2><p>預設以閱讀模式呈現中英對照的形態觀察、影像可見依據與分析限制。</p></div>')
                        with gr.Row(elem_classes=["structured-report-controls"]):
                            report_model_selector = gr.Dropdown(
                                choices=[],
                                value=None,
                                visible=False,
                                interactive=False,
                                label="分析推論模型",
                                info="選擇要查看報告的分析推論模型。",
                                elem_id="structured-model-selector",
                            )
                            report_region_selector = gr.Dropdown(
                                choices=[],
                                value=None,
                                visible=False,
                                interactive=False,
                                label="異常區域",
                                info="選擇該模型已完成分析的異常區域。",
                                elem_id="structured-region-selector",
                            )
                    structured_report = gr.HTML(
                        empty_structured_report(),
                        elem_id="structured-report-view",
                    )

                with gr.Tab("03　個案紀錄"):
                    with gr.Row(elem_classes=["record-toolbar"]):
                        new_case_id = gr.Textbox(
                            label="新個案編號（可留空自動產生）",
                            placeholder="例如 CASE-2026-001",
                            scale=4,
                        )
                        create_case_button = gr.Button("新增個案", variant="primary", scale=1)
                        refresh_button = gr.Button("重新整理", interactive=False, scale=1)
                    history_status = gr.Markdown("", elem_classes=["status-panel"])
                    history = gr.Dataframe(
                        headers=["個案編號", "分析時間", "影像尺寸", "候選數", "主要類別", "最高信心", "狀態", "YOLO 模型", "分析推論模型", "推論狀態"],
                        datatype=["str", "str", "str", "number", "str", "str", "str", "str", "str", "str"],
                        interactive=False,
                        label="個案紀錄；請在紀錄上按滑鼠右鍵選擇載入、編輯或刪除",
                        elem_id="server-case-history",
                    )
                    with gr.Row(elem_classes=["context-action-bridge"]):
                        context_case_id = gr.Textbox(
                            value="",
                            container=False,
                            elem_id="context-case-id-bridge",
                        )
                        context_load_case_button = gr.Button(
                            "載入", elem_id="context-load-case-button"
                        )
                        context_edit_case_button = gr.Button(
                            "修改", elem_id="context-edit-case-button"
                        )
                        context_delete_case_button = gr.Button(
                            "刪除", elem_id="context-delete-case-button"
                        )
                    with gr.Column(elem_classes=["content-card"], elem_id="case-editor"):
                        gr.HTML('<div class="section-heading"><div class="kicker">CASE RECORD EDITOR</div><h2>個案欄位編輯</h2><p>右鍵選擇編輯後，本筆紀錄的所有可編輯欄位會填入下方；最上方個案編號也可直接修改。</p></div>')
                        case_id = gr.Textbox(
                            label="個案編號",
                            interactive=True,
                            placeholder="請先點擊上方紀錄",
                            elem_id="case-id-editor",
                        )
                        with gr.Row():
                            patient_id = gr.Textbox(label="受檢者識別碼")
                            specimen_id = gr.Textbox(label="檢體編號")
                            collection_date = gr.Textbox(label="採檢日期")
                        with gr.Row():
                            specimen_type = gr.Dropdown(["組織切片", "細胞學影像", "放射影像", "其他"], allow_custom_value=True, label="檢體類型")
                            anatomical_site = gr.Textbox(label="解剖部位")
                            stain = gr.Dropdown(["H&E", "IHC", "特殊染色", "不適用", "其他"], allow_custom_value=True, label="染色")
                        with gr.Row():
                            microscopic_findings = gr.Textbox(label="顯微鏡／影像所見", lines=6)
                            ai_summary = gr.Textbox(label="AI 輔助摘要", lines=6)
                        final_diagnosis = gr.Textbox(label="最終診斷", lines=4)
                        notes = gr.Textbox(label="備註與建議", lines=3)
                        with gr.Row():
                            reviewer = gr.Textbox(label="審閱者")
                            report_status = gr.Dropdown(["草稿", "待複核", "已審閱", "已簽核"], value="草稿", label="報告狀態")
                            signed_at = gr.Textbox(label="簽核時間")
                        with gr.Row():
                            save_button = gr.Button(
                                "儲存修改與個案編號",
                                variant="primary",
                                interactive=False,
                            )
                            delete_case_button = gr.Button(
                                "刪除所選個案",
                                variant="stop",
                                elem_id="delete-case-record-button",
                            )
                        save_status = gr.Markdown("")

        method.change(update_otp, method, otp)
        ssh_button.click(
            ssh_login_and_discover,
            [nchc_host, nchc_port, username, password, method, otp, local_port],
            [ssh_session_token, project, account, password, otp, managed_status],
            api_name=False,
        )
        submit_button.click(
            submit_and_connect,
            [ssh_session_token, project, partition, account, walltime, cpus, memory, gpus, local_port],
            [
                connection_token, connection_panel, app_panel, session_banner,
                managed_status, password, otp, localization_model, student_model, analyze_button,
                refresh_button, save_button, student_model_refresh,
            ],
            api_name=False,
        )
        student_model_refresh.click(
            refresh_student_models,
            [connection_token, student_model],
            student_model,
            api_name=False,
        )
        model_refresh_timer.tick(
            poll_student_models,
            [connection_token, student_model],
            student_model,
            show_progress="hidden",
            api_name=False,
        )
        model_refresh_timer.tick(
            structured_model_loading_status,
            connection_token,
            model_loading_view,
            show_progress="hidden",
            api_name=False,
        )
        disconnect_button.click(
            disconnect,
            [connection_token],
            [connection_token, connection_panel, app_panel, session_banner, managed_status],
            api_name=False,
        )
        input_image.upload(
            lambda image: (
                ImageOps.exif_transpose(image).convert("RGB").copy()
                if isinstance(image, Image.Image) else None,
                [],
            ),
            input_image,
            [source_image_state, active_detections],
            queue=False,
            api_name=False,
        )
        analyze_button.click(
            analyze_remote,
            [
                connection_token, source_image_state, confidence, iou, max_detections,
                localization_model, student_model,
            ],
            [
                upload_status, analysis_status, metrics, workspace, vlm_json,
                structured_report, vlm_region_reports, report_model_selector,
                report_region_selector, active_report_detection_index, history,
                active_case_id, case_id,
                ai_summary, report_status, selected_regions, student_analysis_button,
                active_detections,
            ],
            show_progress="full",
            api_name=False,
        )
        student_analysis_button.click(
            analyze_selected_regions,
            [connection_token, active_case_id, student_model, selected_regions],
            [
                upload_status, analysis_status, metrics, workspace, vlm_json,
                structured_report, vlm_region_reports, report_model_selector,
                report_region_selector, active_report_detection_index, history,
                ai_summary, report_status,
            ],
            show_progress="full",
            api_name=False,
        )
        selected_regions.change(
            selected_region_preview,
            [source_image_state, active_detections, active_case_id, student_model, selected_regions],
            [student_analysis_button, input_image],
            queue=False,
            api_name=False,
        )
        student_model.change(
            selected_region_controls,
            [active_case_id, student_model, selected_regions],
            student_analysis_button,
            api_name=False,
        )
        refresh_button.click(
            refresh_history,
            connection_token,
            [history, history_status],
            api_name=False,
        )
        create_case_button.click(
            create_case_record,
            [connection_token, new_case_id],
            [
                history, history_status, new_case_id, active_case_id, case_id,
                input_image, upload_status, analysis_status, metrics, workspace,
                vlm_json, structured_report, vlm_region_reports, report_model_selector,
                report_region_selector, active_report_detection_index, patient_id,
                specimen_id, collection_date,
                specimen_type, anatomical_site, stain, microscopic_findings, ai_summary,
                final_diagnosis, notes, reviewer, report_status, signed_at,
                selected_regions, student_analysis_button, source_image_state, active_detections,
            ],
            api_name=False,
        )
        case_load_outputs = [
            input_image, upload_status, analysis_status, metrics, workspace, vlm_json,
            structured_report, vlm_region_reports, report_model_selector,
            report_region_selector, active_report_detection_index, active_case_id,
            case_id, patient_id,
            specimen_id, collection_date, specimen_type, anatomical_site, stain,
            microscopic_findings, ai_summary, final_diagnosis, notes, reviewer,
            report_status, signed_at, selected_regions, student_analysis_button,
            source_image_state, active_detections,
        ]
        context_load_case_button.click(
            load_history_case,
            [connection_token, context_case_id],
            case_load_outputs,
            api_name=False,
        )
        context_edit_event = context_edit_case_button.click(
            load_history_case,
            [connection_token, context_case_id],
            case_load_outputs,
            api_name=False,
        )
        context_edit_event.then(
            fn=None,
            inputs=None,
            outputs=None,
            js="() => { const editor=document.querySelector('#case-editor'); editor?.scrollIntoView({behavior:'smooth',block:'start'}); window.setTimeout(()=>editor?.querySelector('#case-id-editor input, #case-id-editor textarea')?.focus(),450); }",
            api_name=False,
        )
        report_model_selector.change(
            switch_structured_report_model,
            [
                vlm_region_reports,
                report_model_selector,
                active_report_detection_index,
                active_case_id,
            ],
            [
                vlm_json,
                structured_report,
                active_report_detection_index,
                report_region_selector,
            ],
            api_name=False,
        )
        report_region_selector.change(
            switch_structured_region_report,
            [vlm_region_reports, report_region_selector, active_case_id],
            [
                vlm_json, structured_report, active_report_detection_index,
            ],
            api_name=False,
        )
        save_button.click(
            save_case_record,
            [
                connection_token, active_case_id, case_id, patient_id, specimen_id,
                collection_date, specimen_type, anatomical_site, stain, microscopic_findings,
                ai_summary, final_diagnosis, notes, reviewer, report_status, signed_at,
                active_report_detection_index,
            ],
            [active_case_id, case_id, history, save_status, workspace, structured_report],
            api_name=False,
        )
        context_delete_case_button.click(
            delete_case_record,
            [connection_token, context_case_id],
            [
                history, history_status, active_case_id, case_id, input_image, upload_status,
                analysis_status, metrics, workspace, vlm_json, structured_report,
                vlm_region_reports, report_model_selector, report_region_selector,
                active_report_detection_index, patient_id, specimen_id, collection_date,
                specimen_type, anatomical_site,
                stain, microscopic_findings, ai_summary, final_diagnosis, notes, reviewer,
                report_status, signed_at, selected_regions, student_analysis_button,
                source_image_state, active_detections,
            ],
            api_name=False,
        )
        delete_case_button.click(
            delete_case_record,
            [connection_token, active_case_id],
            [
                history, history_status, active_case_id, case_id, input_image, upload_status,
                analysis_status, metrics, workspace, vlm_json, structured_report,
                vlm_region_reports, report_model_selector, report_region_selector,
                active_report_detection_index, patient_id, specimen_id, collection_date,
                specimen_type, anatomical_site,
                stain, microscopic_findings,
                ai_summary, final_diagnosis, notes, reviewer, report_status, signed_at,
                selected_regions, student_analysis_button, source_image_state, active_detections,
            ],
            api_name=False,
        )

    return demo


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PathoVision localhost REST Client")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8200)
    parser.add_argument(
        "--mcp-host",
        default=os.environ.get("PATHOVISION_MCP_HOST", "127.0.0.1"),
        help="MCP Server bind address (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--mcp-port",
        type=int,
        default=int(os.environ.get("PATHOVISION_MCP_PORT", "8300")),
        help="MCP Server port (default: 8300)",
    )
    parser.add_argument(
        "--mcp-path",
        default=os.environ.get("PATHOVISION_MCP_PATH", "/mcp"),
        help="MCP Streamable HTTP endpoint path (default: /mcp)",
    )
    parser.add_argument(
        "--no-mcp",
        action="store_true",
        help="Run only the Gradio client without starting the MCP Server",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if not args.no_mcp:
        start_mcp_server(args.mcp_host, args.mcp_port, args.mcp_path)
        mcp_path = "/" + args.mcp_path.strip("/")
        print(f"PathoVision MCP Server: http://{args.mcp_host}:{args.mcp_port}{mcp_path}")
    demo = create_ui().queue(default_concurrency_limit=2)
    try:
        demo.launch(
            server_name=args.host,
            server_port=args.port,
            share=False,
            show_api=False,
        )
    finally:
        # Ctrl+C、正常關閉 Python 或 Gradio 結束時，取消所有由本程式提交的 Job。
        _CONNECTIONS.clear()
        clear_mcp_api()
        close_all_sessions(cancel_jobs=True)
