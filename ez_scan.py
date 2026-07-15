"""Fakeable client for the direct, asynchronous EZ-Scan API."""

# This staged file is linted both here and after copying under the real src root.
# ruff: noqa: I001

from __future__ import annotations

import hashlib
import os
import tempfile
import time
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urljoin

import httpx
from pydantic import Field, field_validator

from harden.contracts import StrictModel
from harden.enterprise.contracts import (
    EzScanDownloadResult,
    EzScanJobStatus,
    EzScanOptionField,
    EzScanStatusResult,
    EzScanSubmission,
    EzScanSubmissionRequest,
)

STATUS_ALIASES = {
    "submitted": EzScanJobStatus.SUBMITTED,
    "accepted": EzScanJobStatus.SUBMITTED,
    "pending": EzScanJobStatus.QUEUED,
    "queued": EzScanJobStatus.QUEUED,
    "in_queue": EzScanJobStatus.QUEUED,
    "running": EzScanJobStatus.RUNNING,
    "in_progress": EzScanJobStatus.RUNNING,
    "building": EzScanJobStatus.RUNNING,
    "success": EzScanJobStatus.PASSED,
    "succeeded": EzScanJobStatus.PASSED,
    "passed": EzScanJobStatus.PASSED,
    "complete": EzScanJobStatus.PASSED,
    "completed": EzScanJobStatus.PASSED,
    "failure": EzScanJobStatus.FAILED,
    "failed": EzScanJobStatus.FAILED,
    "error": EzScanJobStatus.FAILED,
    "aborted": EzScanJobStatus.CANCELLED,
    "cancelled": EzScanJobStatus.CANCELLED,
    "canceled": EzScanJobStatus.CANCELLED,
    "timeout": EzScanJobStatus.TIMED_OUT,
    "timed_out": EzScanJobStatus.TIMED_OUT,
}


class EzScanError(RuntimeError):
    """Raised when the direct EZ-Scan boundary fails safely."""


class EzScanHttpConfig(StrictModel):
    """Non-secret connection and polling settings for EZ-Scan."""

    base_url: str
    submit_path: str = "/scan"
    status_path: str = "/status/"
    results_path: str = "/results/"
    timeout_seconds: float = 30.0
    poll_interval_seconds: float = 15.0
    poll_timeout_seconds: float = 3600.0
    max_results_bytes: int = 250 * 1024 * 1024
    ca_bundle: str | None = None
    supported_option_fields: set[EzScanOptionField] = Field(
        default_factory=lambda: set(EzScanOptionField)
    )

    @field_validator("base_url")
    @classmethod
    def require_https(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("EZ-Scan base_url must use HTTPS")
        return value.rstrip("/") + "/"

    @field_validator("submit_path", "status_path", "results_path")
    @classmethod
    def require_relative_path(cls, value: str) -> str:
        if not value.startswith("/") or value.startswith("//"):
            raise ValueError("endpoint paths must start with one slash")
        return value

    @field_validator("timeout_seconds")
    @classmethod
    def validate_request_timeout(cls, value: float) -> float:
        if value <= 0 or value > 300:
            raise ValueError("timeout_seconds must be greater than 0 and at most 300")
        return value

    @field_validator("poll_interval_seconds")
    @classmethod
    def validate_poll_interval(cls, value: float) -> float:
        if value < 1 or value > 300:
            raise ValueError(
                "poll_interval_seconds must be between 1 and 300 seconds"
            )
        return value

    @field_validator("poll_timeout_seconds")
    @classmethod
    def validate_poll_timeout(cls, value: float) -> float:
        if value < 1 or value > 86400:
            raise ValueError(
                "poll_timeout_seconds must be between 1 and 86400 seconds"
            )
        return value

    @field_validator("max_results_bytes")
    @classmethod
    def validate_download_limit(cls, value: int) -> int:
        if value < 1024 or value > 2 * 1024 * 1024 * 1024:
            raise ValueError("max_results_bytes must be between 1 KiB and 2 GiB")
        return value


class EzScanBackend(Protocol):
    """Narrow interface used by future artifact, CLI, and graph layers."""

    def submit(self, request: EzScanSubmissionRequest) -> EzScanSubmission: ...

    def get_status(
        self, submission: EzScanSubmission
    ) -> EzScanStatusResult: ...

    def wait_for_completion(
        self, submission: EzScanSubmission
    ) -> EzScanStatusResult: ...

    def download_results(
        self, submission: EzScanSubmission, destination: Path
    ) -> EzScanDownloadResult: ...


class FakeEzScanBackend:
    """Deterministic backend for higher-level tests with no network behavior."""

    def __init__(
        self,
        statuses: list[EzScanJobStatus] | None = None,
        *,
        fail: str | None = None,
    ) -> None:
        self.statuses = statuses or [EzScanJobStatus.QUEUED]
        self.fail = fail
        self.requests: list[EzScanSubmissionRequest] = []
        self._status_index = 0

    def submit(self, request: EzScanSubmissionRequest) -> EzScanSubmission:
        if self.fail:
            raise EzScanError(self.fail)
        self.requests.append(request)
        return EzScanSubmission(
            run_id=request.run_id,
            job_id=f"fake-{len(self.requests):04d}",
            access_token="fake-per-job-token",
        )

    def get_status(self, submission: EzScanSubmission) -> EzScanStatusResult:
        if self.fail:
            raise EzScanError(self.fail)
        index = min(self._status_index, len(self.statuses) - 1)
        status = self.statuses[index]
        self._status_index += 1
        return EzScanStatusResult(
            run_id=submission.run_id,
            job_id=submission.job_id,
            status=status,
            current_status=status.value,
            current_message=f"Fake EZ-Scan status: {status.value}",
        )

    def wait_for_completion(
        self, submission: EzScanSubmission
    ) -> EzScanStatusResult:
        for _ in range(len(self.statuses)):
            result = self.get_status(submission)
            if result.terminal:
                return result
        raise EzScanError("Fake EZ-Scan did not reach a terminal status")

    def download_results(
        self, submission: EzScanSubmission, destination: Path
    ) -> EzScanDownloadResult:
        if self.fail:
            raise EzScanError(self.fail)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(destination, "w") as archive:
            archive.writestr("README.txt", "fake EZ-Scan results\n")
        content = destination.read_bytes()
        return EzScanDownloadResult(
            run_id=submission.run_id,
            job_id=submission.job_id,
            path=str(destination),
            sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
            content_type="application/zip",
        )


class EzScanHttpBackend:
    """HTTPS client for POST /scan, GET /status/, and GET /results/."""

    def __init__(
        self,
        config: EzScanHttpConfig,
        *,
        client: httpx.Client | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self.sleeper = sleeper
        self.monotonic = monotonic
        self._owns_client = client is None
        verify: bool | str = config.ca_bundle if config.ca_bundle else True
        self.client = client or httpx.Client(
            timeout=config.timeout_seconds,
            verify=verify,
            follow_redirects=False,
        )

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> EzScanHttpBackend:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def submit(self, request: EzScanSubmissionRequest) -> EzScanSubmission:
        payload = self._request_json(
            "POST",
            self.config.submit_path,
            json=self._submission_payload(request),
        )
        return self._submission_from_response(request, payload)

    def get_status(self, submission: EzScanSubmission) -> EzScanStatusResult:
        payload = self._request_json(
            "GET",
            self.config.status_path,
            headers=self._job_headers(submission),
            params={"job_id": submission.job_id},
        )
        return self._status_from_response(submission, payload)

    def wait_for_completion(
        self,
        submission: EzScanSubmission,
        *,
        timeout_seconds: float | None = None,
        poll_interval_seconds: float | None = None,
    ) -> EzScanStatusResult:
        timeout = timeout_seconds or self.config.poll_timeout_seconds
        interval = poll_interval_seconds or self.config.poll_interval_seconds
        if timeout <= 0 or interval <= 0:
            raise ValueError("Polling timeout and interval must be positive")
        deadline = self.monotonic() + timeout
        while True:
            result = self.get_status(submission)
            if result.terminal:
                return result
            remaining = deadline - self.monotonic()
            if remaining <= 0:
                return EzScanStatusResult(
                    run_id=submission.run_id,
                    job_id=submission.job_id,
                    status=EzScanJobStatus.TIMED_OUT,
                    current_status="TimedOut",
                    current_message="Local EZ-Scan polling timeout expired",
                )
            self.sleeper(min(interval, remaining))

    def download_results(
        self,
        submission: EzScanSubmission,
        destination: Path,
    ) -> EzScanDownloadResult:
        destination = destination.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
        )
        temporary_path = Path(temporary_name)
        digest = hashlib.sha256()
        size_bytes = 0
        signature = bytearray()
        content_type: str | None = None
        url = self._url(self.config.results_path)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                try:
                    with self.client.stream(
                        "GET",
                        url,
                        headers=self._job_headers(submission),
                        params={"job_id": submission.job_id},
                    ) as response:
                        response.raise_for_status()
                        content_type = response.headers.get("Content-Type")
                        for chunk in response.iter_bytes():
                            if not chunk:
                                continue
                            size_bytes += len(chunk)
                            if size_bytes > self.config.max_results_bytes:
                                raise EzScanError(
                                    "EZ-Scan results exceeded the configured size limit"
                                )
                            if len(signature) < 4:
                                signature.extend(chunk[: 4 - len(signature)])
                            digest.update(chunk)
                            handle.write(chunk)
                        handle.flush()
                        os.fsync(handle.fileno())
                except httpx.TimeoutException as exc:
                    raise EzScanError("EZ-Scan results download timed out") from exc
                except httpx.HTTPStatusError as exc:
                    raise _http_status_error(exc.response.status_code) from exc
                except httpx.HTTPError as exc:
                    raise EzScanError("EZ-Scan results download failed") from exc
            if bytes(signature[:2]) != b"PK":
                raise EzScanError("EZ-Scan results response was not a ZIP archive")
            os.replace(temporary_path, destination)
        except OSError as exc:
            raise EzScanError("Unable to write EZ-Scan results archive") from exc
        finally:
            temporary_path.unlink(missing_ok=True)
        return EzScanDownloadResult(
            run_id=submission.run_id,
            job_id=submission.job_id,
            path=str(destination),
            sha256=digest.hexdigest(),
            size_bytes=size_bytes,
            content_type=content_type,
        )

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        request_headers = {"Accept": "application/json"}
        if headers:
            request_headers.update(headers)
        try:
            response = self.client.request(
                method,
                self._url(path),
                headers=request_headers,
                params=params,
                json=json,
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise EzScanError("EZ-Scan request timed out") from exc
        except httpx.HTTPStatusError as exc:
            raise _http_status_error(exc.response.status_code) from exc
        except httpx.HTTPError as exc:
            raise EzScanError("EZ-Scan request failed") from exc
        return _required_json_object(response)

    def _url(self, path: str) -> str:
        return urljoin(self.config.base_url, path.lstrip("/"))

    @staticmethod
    def _job_headers(submission: EzScanSubmission) -> dict[str, str]:
        return {"Authorize": submission.access_token.get_secret_value()}

    def _submission_payload(
        self, request: EzScanSubmissionRequest
    ) -> dict[str, str]:
        payload = {
            "container_image": request.container_image,
            "email": request.email_address,
        }
        options = {
            EzScanOptionField.SYFT_SBOM: request.syft_sbom,
            EzScanOptionField.CYCLONEDX_SBOM: request.cyclonedx_sbom,
            EzScanOptionField.TRIVY_SCAN: request.trivy_scan,
            EzScanOptionField.ADDITIONAL_SCAN: request.additional_scan,
        }
        for field, enabled in options.items():
            if field in self.config.supported_option_fields:
                payload[field.value] = _wire_boolean(enabled)
            elif enabled:
                raise EzScanError(
                    f"Requested EZ-Scan option is unsupported by this API profile: "
                    f"{field.value}"
                )
        return payload

    @staticmethod
    def _submission_from_response(
        request: EzScanSubmissionRequest,
        payload: dict[str, Any],
    ) -> EzScanSubmission:
        job_id = _required_scalar(payload, ("job_id",), "job_id")
        access_token = _required_scalar(
            payload,
            ("access_token", "user_access_token"),
            "access_token",
        )
        status_url = _optional_scalar(payload, ("status_url",))
        current_message = _optional_scalar(payload, ("current_message",))
        return EzScanSubmission(
            run_id=request.run_id,
            job_id=job_id,
            access_token=access_token,
            status=EzScanJobStatus.SUBMITTED,
            status_url=status_url,
            current_message=current_message,
        )

    @staticmethod
    def _status_from_response(
        submission: EzScanSubmission,
        payload: dict[str, Any],
    ) -> EzScanStatusResult:
        current_status = _required_scalar(
            payload,
            ("current_status",),
            "current_status",
        )
        current_message = _required_scalar(
            payload,
            ("current_message",),
            "current_message",
        )
        unique_download = _optional_scalar(payload, ("unique_download",))
        return EzScanStatusResult(
            run_id=submission.run_id,
            job_id=submission.job_id,
            status=_normalize_status(current_status),
            current_status=current_status,
            current_message=current_message,
            unique_download=unique_download,
        )


def _wire_boolean(value: bool) -> str:
    return "true" if value else "false"


def _required_json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise EzScanError("EZ-Scan returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise EzScanError("EZ-Scan JSON response must be an object")
    return payload


def _required_scalar(
    payload: dict[str, Any],
    keys: tuple[str, ...],
    label: str,
) -> str:
    value = _optional_scalar(payload, keys)
    if value is None:
        raise EzScanError(f"EZ-Scan response did not include {label}")
    return value


def _optional_scalar(
    payload: dict[str, Any],
    keys: tuple[str, ...],
) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str | int):
            return str(value)
    return None


def _normalize_status(value: Any | None) -> EzScanJobStatus:
    if value is None:
        return EzScanJobStatus.SUBMITTED
    normalized = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    return STATUS_ALIASES.get(normalized, EzScanJobStatus.UNKNOWN)


def _http_status_error(status_code: int) -> EzScanError:
    if status_code == 422:
        return EzScanError("EZ-Scan rejected the scan parameters (HTTP 422)")
    if status_code == 500:
        return EzScanError("EZ-Scan failed to start or process the pipeline (HTTP 500)")
    return EzScanError(f"EZ-Scan returned HTTP {status_code}")
