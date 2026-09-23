"""Validated Blender operations exposed to the MCP layer."""

from __future__ import annotations

import json
import math
import os
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from .bridge import BlenderBridgeClient, BridgeError

_JOB_ID = re.compile(r"[0-9a-f]{32}\Z")


PRIMITIVE_TYPES = frozenset({"CUBE", "UV_SPHERE", "CYLINDER", "CONE", "TORUS", "PLANE"})


class ControlError(RuntimeError):
    """A safe, user-readable control error."""


class BlenderController:
    def __init__(
        self,
        bridge: BlenderBridgeClient,
        audit_log: Path,
        mutations_enabled: bool = False,
    ) -> None:
        self.bridge = bridge
        self.audit_log = audit_log
        self.mutations_enabled = mutations_enabled

    @classmethod
    def from_environment(cls) -> BlenderController:
        token = os.environ.get("BLENDER_BRIDGE_TOKEN", "")
        bridge = BlenderBridgeClient(
            endpoint=os.getenv("BLENDER_BRIDGE_URL", "http://127.0.0.1:8765/command"),
            token=token,
            timeout_seconds=float(os.getenv("BLENDER_BRIDGE_TIMEOUT", "12")),
        )
        return cls(
            bridge=bridge,
            audit_log=Path(
                os.getenv(
                    "BLENDER_MCP_AUDIT_LOG",
                    "/var/log/blender-control-mcp/audit.jsonl",
                )
            ),
            mutations_enabled=os.getenv("BLENDER_MCP_ENABLE_MUTATIONS", "false").lower()
            in {"1", "true", "yes"},
        )

    def _audit(
        self,
        tool: str,
        arguments: dict[str, Any],
        outcome: str,
        error: str | None = None,
    ) -> None:
        record: dict[str, Any] = {
            "time": datetime.now(UTC).isoformat(),
            "tool": tool,
            "arguments": arguments,
            "outcome": outcome,
        }
        if error:
            record["error"] = error
        try:
            self.audit_log.parent.mkdir(parents=True, exist_ok=True)
            with self.audit_log.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def _call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            result = self.bridge.call(tool, arguments)
        except BridgeError as exc:
            self._audit(tool, arguments, "failed", str(exc))
            raise ControlError(str(exc)) from exc
        self._audit(tool, arguments, "success")
        return result

    def _require_mutations(self) -> None:
        if not self.mutations_enabled:
            raise ControlError("Blender mutations are disabled by server policy.")

    @staticmethod
    def _name(value: str) -> str:
        if not isinstance(value, str):
            raise ControlError("Object name must be text.")
        normalized = value.strip()
        if not normalized or len(normalized) > 128 or any(ord(char) < 32 for char in normalized):
            raise ControlError("Object name must contain 1 to 128 printable characters.")
        return normalized

    @staticmethod
    def _vector(
        value: Iterable[float],
        field: str,
        *,
        positive: bool = False,
    ) -> list[float]:
        if isinstance(value, (str, bytes)):
            raise ControlError(f"{field} must contain exactly three numbers.")
        try:
            vector = [float(component) for component in value]
        except (TypeError, ValueError) as exc:
            raise ControlError(f"{field} must contain exactly three numbers.") from exc
        if len(vector) != 3 or not all(math.isfinite(component) for component in vector):
            raise ControlError(f"{field} must contain exactly three finite numbers.")
        if any(abs(component) > 100_000 for component in vector):
            raise ControlError(f"{field} components must not exceed 100000.")
        if positive and any(component <= 0 for component in vector):
            raise ControlError(f"{field} components must be greater than zero.")
        return vector

    def health(self) -> dict[str, Any]:
        return self._call("health", {})

    def get_scene(self) -> dict[str, Any]:
        return self._call("get_scene", {})

    def list_objects(self, limit: int = 100) -> dict[str, Any]:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 200:
            raise ControlError("limit must be an integer between 1 and 200.")
        return self._call("list_objects", {"limit": limit})

    def get_object(self, name: str) -> dict[str, Any]:
        return self._call("get_object", {"name": self._name(name)})

    def start_turntable(self, name: str, views: int = 12) -> dict[str, Any]:
        if not isinstance(views, int) or isinstance(views, bool) or views not in {8, 12, 16, 24}:
            raise ControlError("views must be 8, 12, 16, or 24.")
        return self._call("start_turntable", {"name": self._name(name), "views": views})

    def turntable_status(self, job_id: str) -> dict[str, Any]:
        if not isinstance(job_id, str) or not _JOB_ID.fullmatch(job_id):
            raise ControlError("Invalid turntable job ID.")
        return self._call("turntable_status", {"job_id": job_id})

    def turntable_sheet(self, job_id: str) -> bytes:
        status = self.turntable_status(job_id)
        if status.get("state") != "completed":
            raise ControlError("Turntable is not completed; check status first.")
        count = status.get("views")
        if not isinstance(count, int) or count not in {8, 12, 16, 24}:
            raise ControlError("Invalid turntable view count.")
        from PIL import Image, ImageDraw

        columns = 4
        cell = 320
        rows = (count + columns - 1) // columns
        sheet = Image.new("RGB", (columns * cell, rows * (cell + 24)), "#202020")
        draw = ImageDraw.Draw(sheet)
        for index in range(count):
            try:
                frame = self.bridge.turntable_frame(job_id, index)
                with Image.open(BytesIO(frame)) as image:
                    image.load()
                    if image.width > 1024 or image.height > 1024:
                        raise ControlError("Turntable frame is too large.")
                    thumb = image.convert("RGB")
                    thumb.thumbnail((cell, cell))
                    x = (index % columns) * cell + (cell - thumb.width) // 2
                    y = (index // columns) * (cell + 24) + (cell - thumb.height) // 2
                    sheet.paste(thumb, (x, y))
                    draw.text(
                        ((index % columns) * cell + 8, (index // columns) * (cell + 24) + cell + 3),
                        f"{index * 360 // count}°",
                        fill="white",
                    )
            except (BridgeError, OSError, ValueError) as exc:
                raise ControlError("Could not retrieve a turntable frame.") from exc
        output = BytesIO()
        sheet.save(output, format="PNG")
        self._audit("turntable_sheet", {"job_id": job_id}, "success")
        return output.getvalue()

    def create_primitive(
        self,
        primitive_type: str,
        name: str,
        location: Iterable[float] = (0.0, 0.0, 0.0),
        rotation: Iterable[float] = (0.0, 0.0, 0.0),
        scale: Iterable[float] = (1.0, 1.0, 1.0),
    ) -> dict[str, Any]:
        self._require_mutations()
        normalized_type = primitive_type.strip().upper()
        if normalized_type not in PRIMITIVE_TYPES:
            allowed = ", ".join(sorted(PRIMITIVE_TYPES))
            raise ControlError(f"Unsupported primitive type. Allowed values: {allowed}.")
        arguments = {
            "primitive_type": normalized_type,
            "name": self._name(name),
            "location": self._vector(location, "location"),
            "rotation": self._vector(rotation, "rotation"),
            "scale": self._vector(scale, "scale", positive=True),
        }
        return self._call("create_primitive", arguments)

    def set_transform(
        self,
        name: str,
        location: Iterable[float],
        rotation: Iterable[float],
        scale: Iterable[float],
    ) -> dict[str, Any]:
        self._require_mutations()
        arguments = {
            "name": self._name(name),
            "location": self._vector(location, "location"),
            "rotation": self._vector(rotation, "rotation"),
            "scale": self._vector(scale, "scale", positive=True),
        }
        return self._call("set_transform", arguments)

    def delete_object(self, name: str) -> dict[str, Any]:
        """Delete one exact-name object when mutations are enabled."""
        self._require_mutations()
        return self._call("delete_object", {"name": self._name(name)})

    def delete_collection(self, name: str) -> dict[str, Any]:
        """Delete one exact-name collection while preserving its contents."""
        self._require_mutations()
        return self._call("delete_collection", {"name": self._name(name)})

    def create_collection(self, name: str, object_names: list[str]) -> dict[str, Any]:
        self._require_mutations()
        if not isinstance(object_names, list) or not 1 <= len(object_names) <= 200:
            raise ControlError("object_names must be a list of 1 to 200 object names.")
        normalized = [self._name(item) for item in object_names]
        if len(set(normalized)) != len(normalized):
            raise ControlError("object_names must not contain duplicates.")
        return self._call(
            "create_collection", {"name": self._name(name), "object_names": normalized}
        )
