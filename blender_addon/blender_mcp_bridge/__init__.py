"""Blender add-on providing a localhost-only, token-authenticated command bridge."""

from __future__ import annotations

import hmac
import json
import math
import os
import queue
import shutil
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import bpy
from mathutils import Quaternion, Vector

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
_MAX_MESH_VERTICES = 4_096
_MAX_MESH_EDGES = 8_192
_MAX_MESH_FACES = 4_096
_MAX_FACE_VERTICES = 256
_MAX_FACE_INDEX_REFERENCES = 32_768


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
_previews: dict[str, dict[str, Any]] = {}
_preview_root: Path | None = None
_active_preview: str | None = None
_PREVIEW_TTL = 600


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


def _mesh_topology(
    arguments: dict[str, Any],
) -> tuple[list[list[float]], list[list[int]], list[list[int]]]:
    vertices = arguments.get("vertices")
    if not isinstance(vertices, list) or not 1 <= len(vertices) <= _MAX_MESH_VERTICES:
        raise ValueError(
            f"vertices must be a list containing 1 to {_MAX_MESH_VERTICES} vertices"
        )
    normalized_vertices: list[list[float]] = []
    for index, vertex in enumerate(vertices):
        if (
            not isinstance(vertex, list)
            or len(vertex) != 3
            or any(type(component) not in {int, float} for component in vertex)
        ):
            raise ValueError(f"vertices[{index}] must contain exactly three numbers")
        try:
            normalized = [float(component) for component in vertex]
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"vertices[{index}] must contain exactly three numbers") from exc
        if not all(math.isfinite(component) for component in normalized):
            raise ValueError(f"vertices[{index}] must contain finite numbers")
        if any(abs(component) > 100_000 for component in normalized):
            raise ValueError(f"vertices[{index}] components exceed the allowed range")
        normalized_vertices.append(normalized)

    edges = arguments.get("edges")
    if not isinstance(edges, list) or len(edges) > _MAX_MESH_EDGES:
        raise ValueError(f"edges must be a list containing at most {_MAX_MESH_EDGES} edges")
    normalized_edges: list[list[int]] = []
    seen_edges: set[tuple[int, int]] = set()
    for index, edge in enumerate(edges):
        if not isinstance(edge, list) or len(edge) != 2:
            raise ValueError(f"edges[{index}] must contain exactly two vertex indices")
        if any(type(vertex_index) is not int for vertex_index in edge):
            raise ValueError(f"edges[{index}] must contain integer vertex indices")
        if any(vertex_index < 0 or vertex_index >= len(vertices) for vertex_index in edge):
            raise ValueError(f"edges[{index}] contains an out-of-range vertex index")
        if edge[0] == edge[1]:
            raise ValueError(f"edges[{index}] must reference two distinct vertices")
        canonical = tuple(sorted(edge))
        if canonical in seen_edges:
            raise ValueError("edges must not contain duplicates")
        seen_edges.add(canonical)
        normalized_edges.append(list(edge))

    faces = arguments.get("faces")
    if not isinstance(faces, list) or len(faces) > _MAX_MESH_FACES:
        raise ValueError(f"faces must be a list containing at most {_MAX_MESH_FACES} faces")
    normalized_faces: list[list[int]] = []
    face_index_references = 0
    for index, face in enumerate(faces):
        if not isinstance(face, list) or not 3 <= len(face) <= _MAX_FACE_VERTICES:
            raise ValueError(
                f"faces[{index}] must contain 3 to {_MAX_FACE_VERTICES} vertex indices"
            )
        if any(type(vertex_index) is not int for vertex_index in face):
            raise ValueError(f"faces[{index}] must contain integer vertex indices")
        if any(vertex_index < 0 or vertex_index >= len(vertices) for vertex_index in face):
            raise ValueError(f"faces[{index}] contains an out-of-range vertex index")
        if len(set(face)) != len(face):
            raise ValueError(f"faces[{index}] must not repeat vertex indices")
        face_index_references += len(face)
        if face_index_references > _MAX_FACE_INDEX_REFERENCES:
            raise ValueError(
                f"faces must contain at most {_MAX_FACE_INDEX_REFERENCES} vertex-index references"
            )
        normalized_faces.append(list(face))

    return normalized_vertices, normalized_edges, normalized_faces


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


def _collection_in_scene(collection: bpy.types.Collection, scene: bpy.types.Scene) -> bool:
    def contains(parent: bpy.types.Collection) -> bool:
        return parent == collection or any(contains(child) for child in parent.children)

    return contains(scene.collection)


def _preview_objects(name: str, scene: bpy.types.Scene) -> list[bpy.types.Object]:
    collection = bpy.data.collections.get(name)
    if collection is not None and _collection_in_scene(collection, scene):
        # Prefer the collection when an object shares its name. Include descendants.
        objects = [
            item for item in collection.all_objects
            if scene.objects.get(item.name) is not None
            and item.type in {"MESH", "CURVE", "SURFACE", "FONT", "META"}
        ]
        if not objects:
            raise ValueError("Collection has no geometry objects in the current scene")
        return objects

    obj = scene.objects.get(name)
    if obj is None or obj.type not in {"MESH", "CURVE", "SURFACE", "FONT", "META"}:
        raise ValueError("Choose a geometry object or collection in the current scene")
    return [obj]


def _execute(command: dict[str, Any]) -> dict[str, Any]:
    action = command.get("action")
    arguments = command.get("arguments", {})
    if not isinstance(action, str) or not isinstance(arguments, dict):
        raise ValueError("Invalid bridge command")

    if action == "start_turntable":
        global _active_preview
        if _active_preview is not None:
            raise ValueError("A turntable is already running")
        name = _name(arguments)
        objects = _preview_objects(name, bpy.context.scene)
        views = arguments.get("views")
        if type(views) is not int or views not in {8, 12, 16, 24}:
            raise ValueError("views must be 8, 12, 16, or 24")
        area_info = _find_viewport()
        if area_info is None:
            raise ValueError("Open a Blender window with a 3D viewport first")
        if _preview_root is None:
            raise RuntimeError("Preview storage is unavailable")
        bounds = [obj.matrix_world @ Vector(corner) for obj in objects for corner in obj.bound_box]
        if not bounds:
            raise ValueError("Target bounds cannot be framed")
        minimum = Vector(tuple(min(point[axis] for point in bounds) for axis in range(3)))
        maximum = Vector(tuple(max(point[axis] for point in bounds) for axis in range(3)))
        center = (minimum + maximum) / 2
        radius = max((point - center).length for point in bounds)
        if radius <= 0 or not math.isfinite(radius):
            raise ValueError("Target bounds cannot be framed")
        job_id = uuid.uuid4().hex
        (_preview_root / job_id).mkdir()
        _previews[job_id] = {
            "state": "running",
            "views": views,
            "completed": 0,
            "created": time.monotonic(),
            "center": center,
            "distance": max(2.5 * radius, 0.5),
            "area_info": area_info,
            "name": name,
            "object_names": tuple(obj.name for obj in objects),
        }
        _active_preview = job_id
        return {"job_id": job_id, "state": "running", "views": views, "completed": 0}

    if action == "turntable_status":
        job_id = arguments.get("job_id")
        if (
            not isinstance(job_id, str)
            or len(job_id) != 32
            or any(c not in "0123456789abcdef" for c in job_id)
        ):
            raise ValueError("Invalid turntable job ID")
        job = _previews.get(job_id)
        if job is None:
            raise ValueError("Turntable job was not found or has expired")
        return {key: job[key] for key in ("state", "views", "completed")} | {
            "job_id": job_id,
            "error": job.get("error"),
        }

    if action in {
        "create_primitive",
        "create_mesh",
        "set_transform",
        "create_collection",
        "delete_object",
        "delete_collection",
    } and _active_preview is not None:
        raise ValueError("Scene changes are unavailable while a turntable is running")

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

    if action == "create_mesh":
        name = _name(arguments)
        if bpy.data.objects.get(name) is not None:
            raise ValueError("An object with that name already exists")
        vertices, edges, faces = _mesh_topology(arguments)
        _ensure_object_mode()
        mesh = None
        obj = None
        try:
            mesh = bpy.data.meshes.new(name)
            mesh.from_pydata(vertices, edges, faces)
            mesh.update()
            obj = bpy.data.objects.new(name, mesh)
            bpy.context.scene.collection.objects.link(obj)
            bpy.context.view_layer.update()
        except Exception:
            if obj is not None:
                bpy.data.objects.remove(obj, do_unlink=True)
            if mesh is not None:
                bpy.data.meshes.remove(mesh)
            raise
        return {
            "created": True,
            "object": _serialize_object(obj),
            "vertex_count": len(vertices),
            "edge_count": len(edges),
            "face_count": len(faces),
        }

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

    if action == "delete_object":
        name = _name(arguments)
        obj = bpy.data.objects.get(name)
        if obj is None:
            raise ValueError("Object does not exist")
        object_type = obj.type
        _ensure_object_mode()
        bpy.data.objects.remove(obj, do_unlink=True)
        bpy.context.view_layer.update()
        return {"deleted": True, "name": name, "type": object_type}

    if action == "delete_collection":
        name = _name(arguments)
        collection = bpy.data.collections.get(name)
        scene = bpy.context.scene
        if collection is None or not _collection_in_scene(collection, scene):
            raise ValueError("Collection does not exist in the current scene")
        if collection == scene.collection:
            raise ValueError("The scene root collection cannot be deleted")

        objects = tuple(collection.objects)
        children = tuple(collection.children)
        parents = []

        # A collection datablock can be linked below multiple collections or
        # directly below multiple scenes. Preserve its direct contents at each
        # link location before removing the datablock globally.
        for candidate_scene in bpy.data.scenes:
            parent = candidate_scene.collection
            if parent.children.get(collection.name) is collection:
                parents.append(parent)
        for parent in bpy.data.collections:
            if parent != collection and parent.children.get(collection.name) is collection:
                parents.append(parent)

        for parent in parents:
            for obj in objects:
                if parent.objects.get(obj.name) is None:
                    parent.objects.link(obj)
            for child in children:
                if parent.children.get(child.name) is None:
                    parent.children.link(child)
            parent.children.unlink(collection)

        bpy.data.collections.remove(collection, do_unlink=True)
        bpy.context.view_layer.update()
        return {
            "deleted": True,
            "name": name,
            "preserved_object_count": len(objects),
            "preserved_child_collection_count": len(children),
        }

    if action == "create_collection":
        name = _name(arguments)
        object_names = arguments.get("object_names")
        if not isinstance(object_names, list) or not 1 <= len(object_names) <= 200:
            raise ValueError("object_names must be a list of 1 to 200 object names")
        if any(
            not isinstance(item, str)
            or item != item.strip()
            or not item
            or len(item) > 128
            or any(ord(char) < 32 for char in item)
            for item in object_names
        ):
            raise ValueError("object_names contains an invalid object name")
        if len(set(object_names)) != len(object_names):
            raise ValueError("object_names must not contain duplicates")
        if bpy.data.collections.get(name) is not None:
            raise ValueError("A collection with that name already exists")
        scene = bpy.context.scene
        objects = [scene.objects.get(item) for item in object_names]
        missing = [
            item for item, obj in zip(object_names, objects, strict=True) if obj is None
        ]
        if missing:
            raise ValueError(
                "Objects do not exist in the current scene: " + ", ".join(missing)
            )

        # Only unlink collections belonging to this scene. Preserve links in other scenes.
        scene_collections = set()

        def gather(collection: bpy.types.Collection) -> None:
            scene_collections.add(collection)
            for child in collection.children:
                gather(child)

        gather(scene.collection)
        collection = bpy.data.collections.new(name)
        scene.collection.children.link(collection)
        for obj in objects:
            collection.objects.link(obj)
            for previous in tuple(obj.users_collection):
                if previous is not collection and previous in scene_collections:
                    previous.objects.unlink(obj)
        bpy.context.view_layer.update()
        return {
            "created": True,
            "collection": collection.name,
            "object_names": [obj.name for obj in objects],
            "count": len(objects),
        }

    raise ValueError("Action is not allowed")


def _find_viewport() -> tuple[Any, Any, Any] | None:
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                region = next((item for item in area.regions if item.type == "WINDOW"), None)
                if region is not None:
                    return window, area, region
    return None


def _capture_next_frame() -> None:
    global _active_preview
    job_id = _active_preview
    if job_id is None:
        return
    job = _previews[job_id]
    index = job["completed"]
    window, area, region = job["area_info"]
    view = area.spaces.active.region_3d
    scene = window.scene
    old_view = (
        view.view_location.copy(),
        view.view_rotation.copy(),
        view.view_distance,
        view.view_perspective,
    )
    old_render = (
        scene.render.filepath,
        scene.render.resolution_x,
        scene.render.resolution_y,
        scene.render.resolution_percentage,
        scene.render.image_settings.file_format,
    )
    old_visibility = [(obj, obj.hide_get(view_layer=window.view_layer)) for obj in scene.objects]
    try:
        if area.type != "VIEW_3D" or _preview_root is None:
            raise RuntimeError("The 3D viewport was closed")
        target_names = set(job["object_names"])
        if any(scene.objects.get(name) is None for name in target_names):
            raise RuntimeError("A preview object was removed")
        for obj, _ in old_visibility:
            obj.hide_set(obj.name not in target_names, view_layer=window.view_layer)
        angle = index * 2 * math.pi / job["views"]
        # The view rotates around the world Z axis; a slight elevation reveals depth.
        view.view_perspective = "PERSP"
        view.view_location = job["center"]
        view.view_distance = job["distance"]
        view.view_rotation = Quaternion((0, 0, 1), angle) @ Quaternion((1, 0, 0), math.radians(65))
        path = _preview_root / job_id / f"{index}.png"
        scene.render.filepath = str(path)
        scene.render.resolution_x = 320
        scene.render.resolution_y = 320
        scene.render.resolution_percentage = 100
        scene.render.image_settings.file_format = "PNG"
        with bpy.context.temp_override(window=window, area=area, region=region):
            outcome = bpy.ops.render.opengl(write_still=True, view_context=True)
        if "FINISHED" not in outcome or not path.is_file() or path.stat().st_size > 2_000_000:
            raise RuntimeError("Blender did not produce a bounded viewport image")
        job["completed"] += 1
        if job["completed"] == job["views"]:
            job["state"] = "completed"
            _active_preview = None
    except Exception as exc:
        job["state"] = "failed"
        job["error"] = f"{type(exc).__name__}: {exc}"
        _active_preview = None
    finally:
        for obj, hidden in old_visibility:
            obj.hide_set(hidden, view_layer=window.view_layer)
        view.view_location, view.view_rotation, view.view_distance, view.view_perspective = old_view
        (
            scene.render.filepath,
            scene.render.resolution_x,
            scene.render.resolution_y,
            scene.render.resolution_percentage,
            scene.render.image_settings.file_format,
        ) = old_render


def _expire_previews() -> None:
    for job_id, job in list(_previews.items()):
        if job_id != _active_preview and time.monotonic() - job["created"] > _PREVIEW_TTL:
            del _previews[job_id]
            if _preview_root is not None:
                shutil.rmtree(_preview_root / job_id, ignore_errors=True)


def _process_jobs() -> float:
    global _active_preview
    _expire_previews()
    if _active_preview is not None:
        try:
            _capture_next_frame()
        except Exception as exc:
            job = _previews.get(_active_preview)
            if job is not None:
                job["state"] = "failed"
                job["error"] = f"{type(exc).__name__}: {exc}"
            _active_preview = None
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

    def do_GET(self) -> None:
        if not hmac.compare_digest(
            self.headers.get("Authorization", ""), f"Bearer {_bridge_token}"
        ):
            self.send_error(401)
            return
        parts = self.path.split("/")
        if len(parts) != 4 or parts[1] != "preview" or not parts[3].isdigit():
            self.send_error(404)
            return
        job_id, index = parts[2], int(parts[3])
        job = _previews.get(job_id)
        if (
            job is None
            or job.get("state") != "completed"
            or index >= job["views"]
            or _preview_root is None
        ):
            self.send_error(404)
            return
        path = _preview_root / job_id / f"{index}.png"
        try:
            if not path.is_file() or path.stat().st_size > 2_000_000:
                raise OSError("Missing or oversized preview")
            content = path.read_bytes()
        except OSError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

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
    global _http_server, _http_thread, _bridge_token, _preview_root
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
    _preview_root = Path(tempfile.mkdtemp(prefix="blender-mcp-preview-"))
    _http_server = ThreadingHTTPServer(("127.0.0.1", port), _BridgeHandler)
    _http_thread = threading.Thread(
        target=_http_server.serve_forever,
        name="blender-mcp-bridge",
        daemon=True,
    )
    _http_thread.start()
    bpy.app.timers.register(_process_jobs, first_interval=0.05, persistent=True)


def _stop_bridge() -> None:
    global _http_server, _http_thread, _bridge_token, _preview_root, _active_preview
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
    _active_preview = None
    _previews.clear()
    if _preview_root is not None:
        shutil.rmtree(_preview_root, ignore_errors=True)
        _preview_root = None


def register() -> None:
    _start_bridge()


def unregister() -> None:
    _stop_bridge()
