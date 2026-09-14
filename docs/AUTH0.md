# Auth0 Configuration

Auth0 protects the public MCP endpoint. The separate bridge token protects only the local MCP-to-Blender hop; it is not an OAuth token and must never be sent to ChatGPT or Cloudflare.

## Values used in this guide

Replace the examples consistently:

| Item | Example |
| --- | --- |
| Public MCP endpoint | `https://blender-mcp.example.com/mcp` |
| Auth0 API Identifier | `https://blender-mcp.example.com/mcp` |
| Required API permission | `blender.control` |
| Issuer | `https://YOUR_AUTH0_TENANT.auth0.com/` |

The API Identifier, `MCP_RESOURCE_URL`, `MCP_AUTH_AUDIENCE`, and client-requested audience must match exactly. Treat path and trailing-slash differences as different values.

## 1. Create a dedicated API

In the Auth0 Dashboard:

1. Open **Applications → APIs**.
2. Create a new API named **Blender MCP**.
3. Set **Identifier** to the exact public MCP endpoint.
4. Select `RS256` signing.
5. Add the permission `blender.control` with a clear description.
6. Set a reasonable access-token lifetime for an interactive session.

Do not reuse the Ubuntu MCP API Identifier. Separate identifiers prevent a token intended for one MCP resource from being accepted by the other.

## 2. Configure the MCP client

Use an interactive Authorization Code flow with PKCE. The exact client-registration workflow depends on what the MCP client supports and what is enabled in the Auth0 tenant:

- Prefer a deliberately registered application with exact callback URLs when the client exposes stable registration details.
- Use Dynamic Client Registration only when the MCP client requires it and the tenant has been configured to allow it safely.
- Do not enable password or implicit grants.
- Grant only the Blender MCP API and the `blender.control` permission.

Copy callback URLs exactly from the client connection screen. Do not guess or broaden callback URL patterns.

## 3. Configure the server environment

In `$HOME/.config/blender-mcp/env`:

```dotenv
MCP_RESOURCE_URL=https://blender-mcp.example.com/mcp
MCP_AUTH_AUDIENCE=https://blender-mcp.example.com/mcp
MCP_AUTH_ISSUER=https://YOUR_AUTH0_TENANT.auth0.com/
MCP_AUTH_REQUIRED_SCOPES=blender.control
```

Restart after a change:

```bash
systemctl --user restart blender-control-mcp.service
journalctl --user -u blender-control-mcp.service -n 50 --no-pager
```

## 4. Verify discovery and rejection

After Cloudflare is configured, verify protected-resource metadata:

```bash
curl -i https://blender-mcp.example.com/.well-known/oauth-protected-resource/mcp
```

Expected result: HTTP `200` and JSON metadata describing the protected resource and authorization server.

Verify that the endpoint rejects an unauthenticated request:

```bash
curl -i https://blender-mcp.example.com/mcp
```

Expected result: HTTP `401` with a Bearer challenge. HTTP `200` is a deployment failure.

## 5. Verify token claims

For a test access token, inspect claims locally without pasting the token into websites or logs. Confirm:

- `iss` exactly matches `MCP_AUTH_ISSUER`.
- `aud` contains `MCP_AUTH_AUDIENCE`.
- `exp` is in the future.
- `sub` is present.
- `scope` or `permissions` contains `blender.control`.
- the signing algorithm is `RS256` and signature validation uses the tenant JWKS.

The server performs these checks. Decoding a JWT without verifying its signature is diagnostic only and is not authentication.

## 6. Failure-safe rules

- Never disable authentication to troubleshoot Cloudflare.
- Never substitute an ID token for an API access token.
- Never put Auth0 client secrets in `.env.example`, Git, shell history, screenshots, or issue reports.
- Use a distinct Auth0 API for Blender MCP.
- Revoke and rotate a credential immediately if it appears in a log, commit, screenshot, or chat.

## References

- [Auth0 access tokens](https://auth0.com/docs/secure/tokens/access-tokens/get-access-tokens)
- [Auth0 third-party application security controls](https://auth0.com/docs/get-started/applications/third-party-applications/security-controls)
