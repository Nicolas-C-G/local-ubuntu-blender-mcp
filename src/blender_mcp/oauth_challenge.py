"""Add required OAuth scopes to MCP bearer challenges."""

from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send


class OAuthScopeChallengeMiddleware:
    """Append required scopes to Bearer authentication challenges."""

    def __init__(self, app: ASGIApp, required_scopes: list[str]) -> None:
        self.app = app
        self.scope_value = " ".join(required_scopes)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async def send_with_scope(message: Message) -> None:
            if (
                message["type"] == "http.response.start"
                and message["status"] in {401, 403}
                and self.scope_value
            ):
                message = dict(message)
                headers = list(message.get("headers", []))
                for index, (name, value) in enumerate(headers):
                    if name.lower() != b"www-authenticate":
                        continue
                    challenge = value.decode("latin-1")
                    if challenge.lower().startswith("bearer ") and "scope=" not in challenge.lower():
                        challenge = f'{challenge}, scope="{self.scope_value}"'
                        headers[index] = (name, challenge.encode("latin-1"))
                    break
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_scope)
