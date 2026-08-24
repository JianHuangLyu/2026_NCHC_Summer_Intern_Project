"""HTTP client for the PathoVision REST API."""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

import requests
from PIL import Image


class APIClientError(RuntimeError):
    pass


@dataclass(frozen=True)
class PathoVisionAPI:
    base_url: str
    api_key: str
    # YOLO + a cold 31B VLM request can exceed the old 180 second limit.
    timeout: float = 900.0
    proxy_url: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_url", self.base_url.rstrip("/") + "/")

    @property
    def headers(self) -> dict[str, str]:
        return {"X-API-Key": self.api_key}

    @property
    def proxies(self) -> dict[str, str] | None:
        if not self.proxy_url:
            return None
        return {"http": self.proxy_url, "https": self.proxy_url}

    def _url(self, path: str) -> str:
        return urljoin(self.base_url, path.lstrip("/"))

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        kwargs.setdefault("timeout", self.timeout)
        kwargs.setdefault("proxies", self.proxies)
        headers = dict(self.headers)
        headers.update(kwargs.pop("headers", {}))
        try:
            response = requests.request(method, self._url(path), headers=headers, **kwargs)
        except requests.RequestException as exc:
            raise APIClientError(f"無法連線 REST Server：{exc}") from exc
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            raise APIClientError(f"REST API {response.status_code}：{detail}")
        return response

    def health(self) -> dict[str, Any]:
        try:
            response = requests.get(
                self._url("/healthz"),
                # Startup probes use a short client timeout so a port that has
                # not started listening yet cannot stall the Slurm poll loop.
                timeout=min(float(self.timeout), 15.0),
                proxies=self.proxies,
            )
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            raise APIClientError(f"Server health check 失敗：{exc}") from exc

    def model(self) -> dict[str, Any]:
        return self._request("GET", "/api/v1/model").json()

    def student_models(self) -> list[dict[str, Any]]:
        return self._request("GET", "/api/v1/student-models").json()

    def analyze(
        self,
        image: Image.Image,
        confidence: float,
        iou: float,
        max_detections: int,
        student_model: str | None = None,
        localization_model: str | None = None,
    ) -> dict[str, Any]:
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="PNG")
        files = {"image": ("upload.png", buffer.getvalue(), "image/png")}
        data = {
            "confidence": str(confidence),
            "iou": str(iou),
            "max_detections": str(max_detections),
        }
        if localization_model is not None:
            data["localization_model"] = localization_model
        # None keeps the field absent and lets the Server select its default.
        # The explicit sentinel asks the Server to run YOLO only.
        if student_model is not None:
            data["student_model"] = student_model
        return self._request(
            "POST",
            "/api/v1/analyses",
            files=files,
            data=data,
            timeout=900,
        ).json()

    def analyze_regions(
        self,
        case_id: str,
        student_model: str,
        detection_indices: list[int],
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/v1/analyses/{case_id}/student-analysis",
            json={
                "student_model": student_model,
                "detection_indices": detection_indices,
            },
            timeout=1800,
        ).json()

    def list_analyses(self, limit: int = 100) -> list[dict[str, Any]]:
        return self._request("GET", "/api/v1/analyses", params={"limit": limit}).json()

    def get_analysis(self, case_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/v1/analyses/{case_id}").json()

    def get_image(self, artifact_path: str) -> Image.Image:
        response = self._request("GET", artifact_path)
        with Image.open(io.BytesIO(response.content)) as image:
            return image.convert("RGB").copy()

    def create_case(self, case_id: str = "") -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/v1/cases",
            json={"case_id": case_id},
        ).json()

    def update_case(
        self,
        original_case_id: str,
        new_case_id: str,
        report: dict[str, Any],
    ) -> dict[str, Any]:
        return self._request(
            "PATCH",
            f"/api/v1/analyses/{original_case_id}",
            json={"case_id": new_case_id, "report": report},
        ).json()

    def delete_analysis(self, case_id: str) -> None:
        self._request("DELETE", f"/api/v1/analyses/{case_id}")

    def update_report(self, case_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request(
            "PATCH",
            f"/api/v1/analyses/{case_id}/report",
            json=payload,
        ).json()

    def update_structured_analysis(
        self,
        case_id: str,
        structured_output: dict[str, Any],
        detection_index: int | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"structured_output": structured_output}
        if detection_index is not None:
            payload["detection_index"] = int(detection_index)
        return self._request(
            "PATCH",
            f"/api/v1/analyses/{case_id}/structured-analysis",
            json=payload,
        ).json()
