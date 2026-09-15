"""Blender add-on providing a localhost-only, token-authenticated command bridge."""

from __future__ import annotations

import hmac
import json
import math
import os
import queue
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import bpy


bl_info = {
    "name": "Blender MCP Bridge",
    "author": "Nicolás Cartes",
    "version": (0, 1, 0),
    "blender": (4, 2, 0),
    "location": "Background service",
    "description": "Restricted local bridge for the Blender MCP server",
    "category": "System",
}

_MAX_BODY_BYTES = 65_536


@dataclass
class _Job:
    command: dict[str, Any]
    completed: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)
    response: dict[str, Any] | None = None
    started: bool = False
    cancelled: bool = False


_jobs: queue.Queue[_Job] = queue.Queue(maxsize=64)
_http_server: ThreadingHTTPServer | None = None
_http_thread: threading.Thread | None = None
_bridge_token = ""


def _vector(arguments: dict[str, Any], key: str, *, positive: bool = False) -> list[float]:
    value = arguments.get(key)
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError(f"{key} must contain exactly three numbers")
    vector = [float(component) for component in value]
    if not all(math.isfinite(component) for component in vector):
        raise ValueError(f"{key} must contain finite numbers")
    if any(abs(component) > 100_000 for component in vector):
        raise ValueError(f"{key} components exceed the allowed range")
    if positive and any(component <= 0 for component in vector):
        raise ValueError(f"{key} components must be greater than zero")
    return vector


def _name(arguments: dict[str, Any]) -> str:
    value = arguments.get("name")
    if not isinstance(value, str):
        raise ValueError("name must be text")
    value = value.strip()
    if not value or len(value) > 128 or any(ord(character) < 32 for character in value):
        raise ValueError("name must contain 1 to 128 printable characters")
    return value


def _serialize_object(obj: bpy.types.Object) -> dict[str, Any]:
    return {
        "name": obj.name,
        "type": obj.type,
        "location": list(obj.location),
        "rotation": list(obj.rotation_euler),
        "rotation_mode": obj.rotation_mode,
        "scale": list(obj.scale),
        "visible": not obj.hide_get(),
        "selected": obj.select_get(),
    }


def _ensure_object_mode() -> None:
    active = bpy.context.view_layer.objects.active
    if active is not None and active.mode != "OBJECT":
        result = bpy.ops.object.mode_set(mode="OBJECT")
        if "FINISHED" not in result:
            raise RuntimeError("Could not switch Blender to Object Mode")


def _execute(command: dict[str, Any]) -> dict[str, Any]:
    action = command.get("action")
    arguments = command.get("arguments", {})
    if not isinstance(action, str) or not isinstance(arguments, dict):
        raise ValueError("Invalid bridge command")

    if action == "health":
        return {
            "status": "ok",
            "blender_version": bpy.app.version_string,
            "file": bpy.data.filepath or None,
            "object_count": len(bpy.data.objects),
        }

    if action == "get_scene":
        scene = bpy.context.scene
        active = bpy.context.view_layer.objects.active
        return {
            "scene": scene.name,
            "frame": scene.frame_current,
            "frame_start": scene.frame_start,
            "frame_end": scene.frame_end,
            "render_engine": scene.render.engine,
            "active_camera": scene.camera.name if scene.camera else None,
            "active_object": active.name if active else None,
            "selected_objects": [obj.name for obj in bpy.context.selected_objects],
            "object_count": len(scene.objects),
        }

    if action == "list_objects":
        limit = arguments.get("limit")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 200:
            raise ValueError("limit must be an integer between 1 and 200")
        objects = sorted(bpy.context.scene.objects, key=lambda item: item.name.lower())
        return {
            "objects": [_serialize_object(obj) for obj in objects[:limit]],
            "returned": min(len(objects), limit),
            "total": len(objects),
            "truncated": len(objects) > limit,
        }

    if action == "get_object":
        name = _name(arguments)
        obj = bpy.data.objects.get(name)
        if obj is None:
            raise ValueError("Object does not exist")
        return {"object": _serialize_object(obj)}

    if action == "create_primitive":
        name = _name(arguments)
        if bpy.data.objects.get(name) is not None:
            raise ValueError("An object with that name already exists")
        primitive_type = arguments.get("primitive_type")
        location = _vector(arguments, "location")
        rotation = _vector(arguments, "rotation")
        scale = _vector(arguments, "scale", positive=True)
        operations = {
            "CUBE": bpy.ops.mesh.primitive_cube_add,
            "UV_SPHERE": bpy.ops.mesh.primitive_uv_sphere_add,
            "CYLINDER": bpy.ops.mesh.primitive_cylinder_add,
            "CONE": bpy.ops.mesh.primitive_cone_add,
            "TORUS": bpy.ops.mesh.primitive_torus_add,
            "PLANE": bpy.ops.mesh.primitive_plane_add,
        }
        operation = operations.get(primitive_type)
        if operation is None:
            raise ValueError("Unsupported primitive type")
        _ensure_object_mode()
        result = operation(location=location, rotation=rotation)
        if "FINISHED" not in result or bpy.context.object is None:
            raise RuntimeError("Blender did not create the primitive")
        obj = bpy.context.object
        obj.name = name
        obj.scale = scale
        bpy.context.view_layer.update()
        return {"created": True, "object": _serialize_object(obj)}

    if action == "set_transform":
        name = _name(arguments)
        obj = bpy.data.objects.get(name)
        if obj is None:
            raise ValueError("Object does not exist")
        obj.location = _vector(arguments, "location")
        obj.rotation_euler = _vector(arguments, "rotation")
        obj.scale = _vector(arguments, "scale", positive=True)
        bpy.context.view_layer.update()
        return {"updated": True, "object": _serialize_object(obj)}

    raise ValueError("Action is not allowed")


def _process_jobs() -> float:
    for _ in range(16):
        try:
            job = _jobs.get_nowait()
        except queue.Empty:
            break

        with job.lock:
            if job.cancelled:
                job.response = {"ok": False, "error": "Operation was cancelled"}
                job.completed.set()
                _jobs.task_done()
                continue
            job.started = True

        try:
            response = {"ok": True, "result": _execute(job.command)}
        except Exception as exc:
            response = {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
        with job.lock:
            job.response = response
            job.completed.set()
        _jobs.task_done()
    return 0.05


class _BridgeHandler(BaseHTTPRequestHandler):
    server_version = "BlenderMCPBridge/0.1"

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def _respond(self, status: int, document: dict[str, Any]) -> None:
        encoded = json.dumps(document, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def do_POST(self) -> None:
        if self.path != "/command":
            self._respond(404, {"ok": False, "error": "Not found"})
            return
        expected = f"Bearer {_bridge_token}"
        supplied = self.headers.get("Authorization", "")
        if not hmac.compare_digest(supplied, expected):
            self._respond(401, {"ok": False, "error": "Unauthorized"})
            return
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            content_length = 0
        if not 0 < content_length <= _MAX_BODY_BYTES:
            self._respond(413, {"ok": False, "error": "Invalid request size"})
            return
        if self.headers.get_content_type() != "application/json":
            self._respond(415, {"ok": False, "error": "Content-Type must be application/json"})
            return
        try:
            command = json.loads(self.rfile.read(content_length))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._respond(400, {"ok": False, "error": "Invalid JSON"})
            return
        if not isinstance(command, dict):
            self._respond(400, {"ok": False, "error": "Command must be an object"})
            return

        job = _Job(command=command)
        try:
            _jobs.put_nowait(job)
        except queue.Full:
            self._respond(503, {"ok": False, "error": "Bridge queue is full"})
            return

        if not job.completed.wait(timeout=10):
            with job.lock:
                if not job.started:
                    job.cancelled = True
                    self._respond(
                        504,
                        {"ok": False, "error": "Operation expired before Blender started it"},
                    )
                    return
            # Supported MVP operations are short. Once execution has started, wait for
            # the definitive result so a mutation is never reported as a false failure.
            job.completed.wait()

        with job.lock:
            response = job.response
        if response is None:
            self._respond(500, {"ok": False, "error": "Missing operation result"})
            return
        self._respond(200, response)


def _start_bridge() -> None:
    global _http_server, _http_thread, _bridge_token
    token = os.environ.get("BLENDER_BRIDGE_TOKEN", "")
    if len(token) < 32:
        raise RuntimeError(
            "Set BLENDER_BRIDGE_TOKEN to a secret containing at least 32 characters "
            "before enabling Blender MCP Bridge."
        )
    port = int(os.getenv("BLENDER_BRIDGE_PORT", "8765"))
    if not 1024 <= port <= 65535:
        raise RuntimeError("BLENDER_BRIDGE_PORT must be between 1024 and 65535")
    _bridge_token = token
    _http_server = ThreadingHTTPServer(("127.0.0.1", port), _BridgeHandler)
    _http_thread = threading.Thread(
        target=_http_server.serve_forever,
        name="blender-mcp-bridge",
        daemon=True,
    )
    _http_thread.start()
    bpy.app.timers.register(_process_jobs, first_interval=0.05, persistent=True)


def _stop_bridge() -> None:
    global _http_server, _http_thread, _bridge_token
    if _http_server is not None:
        _http_server.shutdown()
        _http_server.server_close()
    if _http_thread is not None:
        _http_thread.join(timeout=2)
    if bpy.app.timers.is_registered(_process_jobs):
        bpy.app.timers.unregister(_process_jobs)
    _http_server = None
    _http_thread = None
    _bridge_token = ""


def register() -> None:
    _start_bridge()


def unregister() -> None:
    _stop_bridge()
