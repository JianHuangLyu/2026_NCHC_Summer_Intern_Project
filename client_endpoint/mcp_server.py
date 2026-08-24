"""MCP façade for the PathoVision REST API used by the local Gradio app."""

from __future__ import annotations

import logging
import threading
from typing import Any, NoReturn

from mcp.server import MCPServer

from api_client import APIClientError, PathoVisionAPI

LOGGER = logging.getLogger(__name__)

_API_LOCK = threading.RLock()
_ACTIVE_API: PathoVisionAPI | None = None

mcp = MCPServer(
    "PathoVision",
    instructions=(
        "Read PathoVision model metadata and preliminary analysis records from the "
        "REST Server currently connected in the local Gradio app. The returned AI "
        "results are decision-support information and are not a final diagnosis."
    ),
)


def set_api(api: PathoVisionAPI) -> None:
    """Make a successful Gradio REST connection available to MCP tools."""
    global _ACTIVE_API
    with _API_LOCK:
        _ACTIVE_API = api


def clear_api(api: PathoVisionAPI | None = None) -> None:
    """Clear the MCP connection, optionally only when it matches ``api``."""
    global _ACTIVE_API
    with _API_LOCK:
        if api is None or _ACTIVE_API is api:
            _ACTIVE_API = None


def _get_api() -> PathoVisionAPI:
    with _API_LOCK:
        api = _ACTIVE_API
    if api is None:
        raise RuntimeError(
            "PathoVision REST Server is not connected. Connect it in the Gradio UI first."
        )
    return api


def _raise_tool_error(exc: APIClientError) -> NoReturn:
    raise RuntimeError(str(exc)) from exc


@mcp.tool()
def connection_status() -> dict[str, Any]:
    """Check whether PathoVision is connected and return REST health and model metadata."""
    with _API_LOCK:
        api = _ACTIVE_API
    if api is None:
        return {"connected": False, "message": "Connect the REST Server in the Gradio UI."}
    try:
        return {
            "connected": True,
            "base_url": api.base_url,
            "health": api.health(),
            "model": api.model(),
        }
    except APIClientError as exc:
        _raise_tool_error(exc)


@mcp.tool()
def list_analyses(limit: int = 100) -> list[dict[str, Any]]:
    """List recent preliminary PathoVision analysis records, newest first."""
    if not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")
    try:
        return _get_api().list_analyses(limit=limit)
    except APIClientError as exc:
        _raise_tool_error(exc)


@mcp.tool()
def get_analysis(case_id: str) -> dict[str, Any]:
    """Get one preliminary PathoVision analysis record by case ID."""
    normalized_case_id = case_id.strip()
    if not normalized_case_id:
        raise ValueError("case_id is required")
    try:
        return _get_api().get_analysis(normalized_case_id)
    except APIClientError as exc:
        _raise_tool_error(exc)


def start_mcp_server(host: str, port: int, path: str = "/mcp") -> threading.Thread:
    """Start the localhost Streamable HTTP MCP server on a daemon thread."""
    if not 1 <= port <= 65535:
        raise ValueError("MCP port must be between 1 and 65535")
    normalized_path = "/" + path.strip("/")

    def run() -> None:
        try:
            mcp.run(
                transport="streamable-http",
                host=host,
                port=port,
                streamable_http_path=normalized_path,
                stateless_http=True,
                json_response=True,
            )
        except Exception:
            LOGGER.exception("PathoVision MCP Server stopped unexpectedly")

    thread = threading.Thread(target=run, name="pathovision-mcp", daemon=True)
    thread.start()
    return thread
