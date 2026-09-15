"""OAuth 2.1 resource-server authentication for Blender MCP."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import jwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWTError
from mcp.server.auth.provider import AccessToken, TokenVerifier

from .auth_helpers import audience_contains, normalize_issuer, parse_scopes

logger = logging.getLogger(__name__)


class OIDCJWTVerifier(TokenVerifier):
    """Validate signed OIDC access tokens using the provider's JWKS."""

    def __init__(
        self,
        issuer: str,
        audience: str,
        jwks_url: str,
        required_scopes: set[str],
        algorithms: tuple[str, ...] = ("RS256",),
    ) -> None:
        self.issuer = normalize_issuer(issuer)
        self.audience = audience
        self.required_scopes = required_scopes
        self.algorithms = algorithms
        self._jwks_client = PyJWKClient(jwks_url, cache_keys=True, lifespan=3600)

    @classmethod
    def from_environment(cls) -> "OIDCJWTVerifier":
        issuer = normalize_issuer(os.environ["MCP_AUTH_ISSUER"])
        audience = os.environ["MCP_AUTH_AUDIENCE"].strip()
        if not audience.startswith("https://"):
            raise ValueError("MCP_AUTH_AUDIENCE must use HTTPS.")
        jwks_url = os.getenv(
            "MCP_AUTH_JWKS_URL",
            f"{issuer}.well-known/jwks.json",
        ).strip()
        required_scopes = {
            item
            for item in os.getenv(
                "MCP_AUTH_REQUIRED_SCOPES",
                "blender.control",
            ).split()
            if item
        }
        if not required_scopes:
            raise ValueError("At least one OAuth scope is required.")
        return cls(issuer, audience, jwks_url, required_scopes)

    async def verify_token(self, token: str) -> AccessToken | None:
        return await asyncio.to_thread(self._verify_sync, token)

    def _verify_sync(self, token: str) -> AccessToken | None:
        try:
            logger.info("OAuth bearer token received; starting JWT verification")
            header = jwt.get_unverified_header(token)
            if header.get("alg") not in self.algorithms:
                logger.warning("OAuth token rejected: unsupported signing algorithm")
                return None
            signing_key = self._jwks_client.get_signing_key_from_jwt(token).key
            claims: dict[str, Any] = jwt.decode(
                token,
                signing_key,
                algorithms=list(self.algorithms),
                audience=self.audience,
                issuer=self.issuer,
                options={"require": ["exp", "iat", "sub", "aud", "iss"]},
            )
        except (PyJWTError, OSError, ValueError, KeyError, TypeError) as exc:
            logger.warning(
                "OAuth token rejected during JWT validation: %s",
                type(exc).__name__,
            )
            return None

        if not audience_contains(claims.get("aud"), self.audience):
            logger.warning("OAuth token rejected: audience mismatch")
            return None
        scopes = parse_scopes(claims)
        if not self.required_scopes.issubset(scopes):
            missing = sorted(self.required_scopes.difference(scopes))
            logger.warning("OAuth token rejected: missing scopes %s", missing)
            return None

        client_id = claims.get("client_id") or claims.get("azp") or claims["sub"]
        return AccessToken(
            token=token,
            client_id=str(client_id),
            scopes=scopes,
            expires_at=int(claims["exp"]),
            resource=self.audience,
            subject=str(claims["sub"]),
            claims=claims,
        )
