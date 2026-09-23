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
MODIFIER_PARAMETER_SPECS: dict[str, dict[str, tuple[Any, ...]]] = {
    "ARRAY": {
        "count": ("integer", 1, 1_000),
        "use_relative_offset": ("boolean",),
        "relative_offset_displace": ("vector",),
        "use_constant_offset": ("boolean",),
        "constant_offset_displace": ("vector",),
        "use_merge_vertices": ("boolean",),
        "merge_threshold": ("number", 0.0, 100_000.0),
    },
    "BEVEL": {
        "width": ("number", 0.0, 100_000.0),
        "segments": ("integer", 1, 64),
        "limit_method": ("enum", frozenset({"NONE", "ANGLE", "WEIGHT", "VGROUP"})),
        "angle_limit": ("number", 0.0, math.pi),
        "affect": ("enum", frozenset({"EDGES", "VERTICES"})),
        "use_clamp_overlap": ("boolean",),
    },
    "BOOLEAN": {
        "object": ("object_name",),
        "operation": ("enum", frozenset({"DIFFERENCE", "INTERSECT", "UNION"})),
        "solver": ("enum", frozenset({"EXACT", "FAST"})),
    },
    "DECIMATE": {
        "decimate_type": ("enum", frozenset({"COLLAPSE", "DISSOLVE", "UNSUBDIV"})),
        "ratio": ("number", 0.0, 1.0),
        "iterations": ("integer", 0, 100),
        "angle_limit": ("number", 0.0, math.pi),
        "use_collapse_triangulate": ("boolean",),
    },
    "MIRROR": {
        "use_axis": ("bool_vector",),
        "use_clip": ("boolean",),
        "use_mirror_merge": ("boolean",),
        "merge_threshold": ("number", 0.0, 100_000.0),
        "use_bisect_axis": ("bool_vector",),
        "use_bisect_flip_axis": ("bool_vector",),
        "mirror_object": ("object_name",),
    },
    "SCREW": {
        "screw_offset": ("number", -100_000.0, 100_000.0),
        "angle": ("number", -100.0 * math.pi, 100.0 * math.pi),
        "steps": ("integer", 2, 1_024),
        "render_steps": ("integer", 2, 1_024),
        "iterations": ("integer", 1, 100),
        "axis": ("enum", frozenset({"X", "Y", "Z"})),
        "use_smooth_shade": ("boolean",),
        "use_merge_vertices": ("boolean",),
        "merge_threshold": ("number", 0.0, 100_000.0),
    },
    "SIMPLE_DEFORM": {
        "deform_method": ("enum", frozenset({"BEND", "STRETCH", "TAPER", "TWIST"})),
        "deform_axis": ("enum", frozenset({"X", "Y", "Z"})),
        "angle": ("number", -100.0 * math.pi, 100.0 * math.pi),
        "factor": ("number", -100.0, 100.0),
        "limits": ("number_pair", 0.0, 1.0),
        "lock_x": ("boolean",),
        "lock_y": ("boolean",),
        "lock_z": ("boolean",),
    },
    "SOLIDIFY": {
        "thickness": ("number", -100_000.0, 100_000.0),
        "offset": ("number", -1.0, 1.0),
        "use_even_offset": ("boolean",),
        "use_quality_normals": ("boolean",),
    },
    "SUBSURF": {
        "levels": ("integer", 0, 6),
        "render_levels": ("integer", 0, 6),
        "subdivision_type": ("enum", frozenset({"CATMULL_CLARK", "SIMPLE"})),
        "use_limit_surface": ("boolean",),
    },
    "TRIANGULATE": {
        "quad_method": (
            "enum",
            frozenset({"BEAUTY", "FIXED", "FIXED_ALTERNATE", "SHORTEST_DIAGONAL"}),
        ),
        "ngon_method": ("enum", frozenset({"BEAUTY", "CLIP"})),
        "min_vertices": ("integer", 4, 10_000),
    },
}
MODIFIER_TYPES = frozenset(MODIFIER_PARAMETER_SPECS)
MODIFIER_REQUIRED_PARAMETERS = {"BOOLEAN": frozenset({"object"})}
MAX_MESH_VERTICES = 4_096
MAX_MESH_EDGES = 8_192
MAX_MESH_FACES = 4_096
MAX_FACE_VERTICES = 256
MAX_FACE_INDEX_REFERENCES = 32_768


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
        except (TypeError, ValueError, OverflowError) as exc:
            raise ControlError(f"{field} must contain exactly three numbers.") from exc
        if len(vector) != 3 or not all(math.isfinite(component) for component in vector):
            raise ControlError(f"{field} must contain exactly three finite numbers.")
        if any(abs(component) > 100_000 for component in vector):
            raise ControlError(f"{field} components must not exceed 100000.")
        if positive and any(component <= 0 for component in vector):
            raise ControlError(f"{field} components must be greater than zero.")
        return vector

    @classmethod
    def _modifier_parameters(
        cls,
        modifier_type: str,
        parameters: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if parameters is None:
            parameters = {}
        if not isinstance(parameters, dict) or len(parameters) > 16:
            raise ControlError("parameters must be an object with at most 16 entries.")

        specs = MODIFIER_PARAMETER_SPECS[modifier_type]
        if any(not isinstance(key, str) or key not in specs for key in parameters):
            allowed = ", ".join(sorted(specs))
            raise ControlError(
                f"Unsupported parameter for {modifier_type}. Allowed parameters: {allowed}."
            )
        missing = MODIFIER_REQUIRED_PARAMETERS.get(modifier_type, frozenset()) - parameters.keys()
        if missing:
            raise ControlError(
                f"{modifier_type} requires parameter(s): {', '.join(sorted(missing))}."
            )

        normalized: dict[str, Any] = {}
        for key, value in parameters.items():
            spec = specs[key]
            kind = spec[0]
            if kind == "boolean":
                if type(value) is not bool:
                    raise ControlError(f"parameters.{key} must be true or false.")
                normalized[key] = value
            elif kind == "integer":
                if type(value) is not int or not spec[1] <= value <= spec[2]:
                    raise ControlError(
                        f"parameters.{key} must be an integer between {spec[1]} and {spec[2]}."
                    )
                normalized[key] = value
            elif kind == "number":
                if type(value) not in {int, float}:
                    raise ControlError(f"parameters.{key} must be a number.")
                number = float(value)
                if not math.isfinite(number) or not spec[1] <= number <= spec[2]:
                    raise ControlError(
                        f"parameters.{key} must be between {spec[1]} and {spec[2]}."
                    )
                normalized[key] = number
            elif kind == "enum":
                if not isinstance(value, str) or value.strip().upper() not in spec[1]:
                    allowed = ", ".join(sorted(spec[1]))
                    raise ControlError(f"parameters.{key} must be one of: {allowed}.")
                normalized[key] = value.strip().upper()
            elif kind == "vector":
                if (
                    not isinstance(value, list)
                    or len(value) != 3
                    or any(type(component) not in {int, float} for component in value)
                ):
                    raise ControlError(f"parameters.{key} must contain exactly three numbers.")
                normalized[key] = cls._vector(value, f"parameters.{key}")
            elif kind == "bool_vector":
                if (
                    not isinstance(value, list)
                    or len(value) != 3
                    or any(type(component) is not bool for component in value)
                ):
                    raise ControlError(f"parameters.{key} must contain exactly three booleans.")
                if key == "use_axis" and not any(value):
                    raise ControlError("parameters.use_axis must enable at least one axis.")
                normalized[key] = list(value)
            elif kind == "number_pair":
                if (
                    not isinstance(value, list)
                    or len(value) != 2
                    or any(type(component) not in {int, float} for component in value)
                ):
                    raise ControlError(f"parameters.{key} must contain exactly two numbers.")
                pair = [float(component) for component in value]
                if (
                    not all(math.isfinite(component) for component in pair)
                    or not spec[1] <= pair[0] <= pair[1] <= spec[2]
                ):
                    raise ControlError(
                        f"parameters.{key} must be ordered between {spec[1]} and {spec[2]}."
                    )
                normalized[key] = pair
            elif kind == "object_name":
                normalized[key] = cls._name(value)
            else:  # pragma: no cover - specs are defined in this module
                raise ControlError("Invalid modifier parameter specification.")
        return normalized

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

    def create_mesh(
        self,
        vertices: list[list[float]],
        edges: list[list[int]],
        faces: list[list[int]],
        name: str = "CustomMesh",
    ) -> dict[str, Any]:
        """Create one bounded mesh from explicit zero-based topology."""
        self._require_mutations()
        if not isinstance(vertices, list) or not 1 <= len(vertices) <= MAX_MESH_VERTICES:
            raise ControlError(
                f"vertices must be a list containing 1 to {MAX_MESH_VERTICES} vertices."
            )

        normalized_vertices: list[list[float]] = []
        for index, vertex in enumerate(vertices):
            if (
                not isinstance(vertex, list)
                or len(vertex) != 3
                or any(isinstance(component, bool) for component in vertex)
            ):
                raise ControlError(f"vertices[{index}] must contain exactly three numbers.")
            normalized_vertices.append(self._vector(vertex, f"vertices[{index}]"))

        if not isinstance(edges, list) or len(edges) > MAX_MESH_EDGES:
            raise ControlError(f"edges must be a list containing at most {MAX_MESH_EDGES} edges.")
        normalized_edges: list[list[int]] = []
        seen_edges: set[tuple[int, int]] = set()
        for index, edge in enumerate(edges):
            if not isinstance(edge, list) or len(edge) != 2:
                raise ControlError(f"edges[{index}] must contain exactly two vertex indices.")
            if any(type(vertex_index) is not int for vertex_index in edge):
                raise ControlError(f"edges[{index}] must contain integer vertex indices.")
            if any(
                vertex_index < 0 or vertex_index >= len(normalized_vertices)
                for vertex_index in edge
            ):
                raise ControlError(f"edges[{index}] contains an out-of-range vertex index.")
            if edge[0] == edge[1]:
                raise ControlError(f"edges[{index}] must reference two distinct vertices.")
            canonical = tuple(sorted(edge))
            if canonical in seen_edges:
                raise ControlError("edges must not contain duplicates.")
            seen_edges.add(canonical)
            normalized_edges.append(list(edge))

        if not isinstance(faces, list) or len(faces) > MAX_MESH_FACES:
            raise ControlError(f"faces must be a list containing at most {MAX_MESH_FACES} faces.")
        normalized_faces: list[list[int]] = []
        face_index_references = 0
        for index, face in enumerate(faces):
            if not isinstance(face, list) or not 3 <= len(face) <= MAX_FACE_VERTICES:
                raise ControlError(
                    f"faces[{index}] must contain 3 to {MAX_FACE_VERTICES} vertex indices."
                )
            if any(type(vertex_index) is not int for vertex_index in face):
                raise ControlError(f"faces[{index}] must contain integer vertex indices.")
            if any(
                vertex_index < 0 or vertex_index >= len(normalized_vertices)
                for vertex_index in face
            ):
                raise ControlError(f"faces[{index}] contains an out-of-range vertex index.")
            if len(set(face)) != len(face):
                raise ControlError(f"faces[{index}] must not repeat vertex indices.")
            face_index_references += len(face)
            if face_index_references > MAX_FACE_INDEX_REFERENCES:
                raise ControlError(
                    "faces must contain at most "
                    f"{MAX_FACE_INDEX_REFERENCES} vertex-index references."
                )
            normalized_faces.append(list(face))

        return self._call(
            "create_mesh",
            {
                "name": self._name(name),
                "vertices": normalized_vertices,
                "edges": normalized_edges,
                "faces": normalized_faces,
            },
        )

    def add_modifier(
        self,
        object_name: str,
        modifier_type: str,
        parameters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Add one allowlisted, bounded modifier to a mesh object."""
        self._require_mutations()
        if not isinstance(modifier_type, str):
            raise ControlError("modifier_type must be text.")
        normalized_type = modifier_type.strip().upper()
        if normalized_type not in MODIFIER_TYPES:
            allowed = ", ".join(sorted(MODIFIER_TYPES))
            raise ControlError(f"Unsupported modifier type. Allowed values: {allowed}.")
        return self._call(
            "add_modifier",
            {
                "object_name": self._name(object_name),
                "modifier_type": normalized_type,
                "parameters": self._modifier_parameters(normalized_type, parameters),
            },
        )

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
