"""Restricted HTTP client for the local Blender add-on bridge."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

MAX_REQUEST_BYTES = 65_536
MAX_RESPONSE_BYTES = 1_048_576
ALLOWED_ACTIONS = frozenset(
    {
        "health",
        "get_scene",
        "list_objects",
        "get_object",
        "create_primitive",
        "set_transform",
        "start_turntable",
        "turntable_status",
        "create_collection",
    }
)
_JOB_ID = re.compile(r"[0-9a-f]{32}\Z")
MAX_IMAGE_BYTES = 2_000_000


class BridgeError(RuntimeError):
    """A safe, user-readable Blender bridge error."""


@dataclass(frozen=True)
class BlenderBridgeClient:
    endpoint: str
    token: str
    timeout_seconds: float = 12.0

    def __post_init__(self) -> None:
        parsed = urlparse(self.endpoint)
        if parsed.scheme != "http" or parsed.hostname not in {
            "127.0.0.1",
            "localhost",
            "::1",
        }:
            raise ValueError("The Blender bridge must use an HTTP loopback address.")
        if parsed.path not in {"", "/", "/command"}:
            raise ValueError("The Blender bridge endpoint path must be /command.")
        if len(self.token) < 32:
            raise ValueError("BLENDER_BRIDGE_TOKEN must contain at least 32 characters.")
        if not 0.1 <= self.timeout_seconds <= 60:
            raise ValueError("Bridge timeout must be between 0.1 and 60 seconds.")

    def call(self, action: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        if action not in ALLOWED_ACTIONS:
            raise BridgeError("The requested Blender action is not allowed.")

        payload = json.dumps(
            {"action": action, "arguments": arguments or {}},
            separators=(",", ":"),
        ).encode("utf-8")
        if len(payload) > MAX_REQUEST_BYTES:
            raise BridgeError("The Blender request exceeds the bridge size limit.")

        endpoint = self.endpoint.rstrip("/")
        if not endpoint.endswith("/command"):
            endpoint += "/command"
        request = Request(
            endpoint,
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )

        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            detail = exc.read(4096).decode("utf-8", errors="replace")
            raise BridgeError(
                f"Blender bridge rejected the request with HTTP {exc.code}: {detail}"
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise BridgeError(
                "Blender is unavailable. Ensure Blender is open and the bridge add-on is enabled."
            ) from exc

        if len(raw) > MAX_RESPONSE_BYTES:
            raise BridgeError("The Blender bridge response exceeds the size limit.")
        try:
            document = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BridgeError("The Blender bridge returned invalid JSON.") from exc
        if not isinstance(document, dict) or not isinstance(document.get("ok"), bool):
            raise BridgeError("The Blender bridge returned an invalid response.")
        if not document["ok"]:
            raise BridgeError(str(document.get("error") or "Blender operation failed."))

        result = document.get("result", {})
        if not isinstance(result, dict):
            raise BridgeError("The Blender bridge result must be a JSON object.")
        return result

    def turntable_frame(self, job_id: str, index: int) -> bytes:
        if not _JOB_ID.fullmatch(job_id) or not isinstance(index, int) or not 0 <= index < 24:
            raise BridgeError("Invalid turntable frame request.")
        endpoint = self.endpoint.rstrip("/")
        if endpoint.endswith("/command"):
            endpoint = endpoint[: -len("/command")]
        request = Request(
            f"{endpoint}/preview/{job_id}/{index}",
            headers={"Authorization": f"Bearer {self.token}", "Accept": "image/png"},
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                if response.headers.get_content_type() != "image/png":
                    raise BridgeError("Blender bridge returned a non-PNG preview.")
                raw = response.read(MAX_IMAGE_BYTES + 1)
        except HTTPError as exc:
            raise BridgeError(f"Preview image unavailable (HTTP {exc.code}).") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise BridgeError("Blender bridge is unavailable.") from exc
        if len(raw) > MAX_IMAGE_BYTES or not raw.startswith(b"\x89PNG\r\n\x1a\n"):
            raise BridgeError("Preview image is too large or invalid.")
        return raw
