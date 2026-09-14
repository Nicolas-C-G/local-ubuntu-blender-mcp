"""Small OAuth claim helpers shared by the Blender MCP verifier."""

from __future__ import annotations

from typing import Any


def normalize_issuer(issuer: str) -> str:
    normalized = issuer.strip()
    if not normalized.startswith("https://"):
        raise ValueError("MCP_AUTH_ISSUER must use HTTPS.")
    return normalized.rstrip("/") + "/"


def audience_contains(claim_audience: Any, expected: str) -> bool:
    if isinstance(claim_audience, str):
        return claim_audience == expected
    if isinstance(claim_audience, list):
        return expected in claim_audience
    return False


def parse_scopes(claims: dict[str, Any]) -> list[str]:
    raw = claims.get("scope", "")
    scopes = set(raw.split()) if isinstance(raw, str) else set()
    permissions = claims.get("permissions", [])
    if isinstance(permissions, list):
        scopes.update(item for item in permissions if isinstance(item, str))
    return sorted(scopes)
