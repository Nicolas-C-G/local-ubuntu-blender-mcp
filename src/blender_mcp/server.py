"""Authenticated MCP interface for constrained Blender control."""

from __future__ import annotations

import os
from typing import Any

import uvicorn
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import Image, MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl

from .auth import OIDCJWTVerifier
from .auth_helpers import normalize_issuer
from .controller import BlenderController
from .oauth_challenge import OAuthScopeChallengeMiddleware

controller = BlenderController.from_environment()
issuer = normalize_issuer(os.environ["MCP_AUTH_ISSUER"])
resource_url = os.environ["MCP_RESOURCE_URL"].strip()
required_scopes = [
    item for item in os.getenv("MCP_AUTH_REQUIRED_SCOPES", "blender.control").split() if item
]
allowed_hosts = [
    item.strip()
    for item in os.getenv(
        "MCP_ALLOWED_HOSTS",
        "127.0.0.1,127.0.0.1:*,localhost,localhost:*,[::1],[::1]:*",
    ).split(",")
    if item.strip()
]
allowed_origins = [
    item.strip() for item in os.getenv("MCP_ALLOWED_ORIGINS", "").split(",") if item.strip()
]

transport_security = TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=allowed_hosts,
    allowed_origins=allowed_origins,
)
mcp = MCPServer(
    "blender-control",
    instructions=(
        "Inspect and modify Blender only through the explicit tools exposed here. "
        "Never claim an operation succeeded unless its result confirms success. "
        "Mutating tools may be disabled by server policy. Explain the intended "
        "scene change and obtain user confirmation before calling a mutating tool. "
        "There is no arbitrary Python execution tool."
    ),
    token_verifier=OIDCJWTVerifier.from_environment(),
    auth=AuthSettings(
        issuer_url=AnyHttpUrl(issuer),
        resource_server_url=AnyHttpUrl(resource_url),
        required_scopes=required_scopes,
        validate_token_resource=True,
    ),
)


@mcp.tool()
def blender_health() -> dict[str, Any]:
    """Check whether Blender and the local bridge are available."""
    return controller.health()


@mcp.tool()
def blender_get_scene() -> dict[str, Any]:
    """Read a summary of the currently open Blender scene."""
    return controller.get_scene()


@mcp.tool()
def blender_list_objects(limit: int = 100) -> dict[str, Any]:
    """List up to 200 objects in the currently open Blender scene."""
    return controller.list_objects(limit)


@mcp.tool()
def blender_get_object(name: str) -> dict[str, Any]:
    """Read type, transform, visibility, and selection state for one object."""
    return controller.get_object(name)


@mcp.tool()
def blender_turntable_start(name: str, views: int = 12) -> dict[str, Any]:
    """Start a bounded 360-degree viewport preview of a geometry object or collection.

    Use blender_turntable_status until completed, then blender_turntable_sheet.
    Blender must have an open 3D viewport. The scene cannot be changed during capture.
    """
    return controller.start_turntable(name, views)


@mcp.tool()
def blender_turntable_status(job_id: str) -> dict[str, Any]:
    """Check progress of a 360-degree viewport preview job."""
    return controller.turntable_status(job_id)


@mcp.tool()
def blender_turntable_sheet(job_id: str) -> Image:
    """Return a completed object's or collection's 360-degree contact sheet as a PNG image."""
    return Image(data=controller.turntable_sheet(job_id), format="png")


@mcp.tool()
def blender_create_primitive(
    primitive_type: str,
    name: str,
    location: list[float] | None = None,
    rotation: list[float] | None = None,
    scale: list[float] | None = None,
) -> dict[str, Any]:
    """Create an allowlisted mesh primitive when mutations are enabled.

    Rotation values use radians. Supported types are CUBE, UV_SPHERE,
    CYLINDER, CONE, TORUS, and PLANE.
    """
    return controller.create_primitive(
        primitive_type,
        name,
        location if location is not None else [0.0, 0.0, 0.0],
        rotation if rotation is not None else [0.0, 0.0, 0.0],
        scale if scale is not None else [1.0, 1.0, 1.0],
    )


@mcp.tool()
def blender_set_transform(
    name: str,
    location: list[float],
    rotation: list[float],
    scale: list[float],
) -> dict[str, Any]:
    """Replace an object's transform when mutations are enabled.

    Rotation values use radians and every scale component must be positive.
    """
    return controller.set_transform(name, location, rotation, scale)


@mcp.tool()
def blender_create_collection(name: str, object_names: list[str]) -> dict[str, Any]:
    """Create a collection in the current scene and move the named objects into it.

    Requires mutations enabled. Every object must exist in the current scene;
    duplicate names or an existing collection name cause the call to fail.
    """
    return controller.create_collection(name, object_names)


def main() -> None:
    port = int(os.getenv("BLENDER_MCP_PORT", "8001"))
    if not 1024 <= port <= 65535:
        raise ValueError("BLENDER_MCP_PORT must be between 1024 and 65535.")
    app = mcp.streamable_http_app(
        streamable_http_path="/mcp",
        json_response=True,
        transport_security=transport_security,
        host="127.0.0.1",
    )
    app = OAuthScopeChallengeMiddleware(app, required_scopes)
    uvicorn.run(app, host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
