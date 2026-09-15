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

## 2. Enable RBAC and put permissions in access tokens

Open **Applications -> APIs -> Blender MCP -> Settings**. Under **RBAC
Settings**, enable both:

- **Enable RBAC**;
- **Add Permissions in the Access Token**.

Creating `blender.control` does not grant it to a user. Complete both parts of
the assignment:

1. Open **User Management -> Roles** and create or open **Blender Operator**.
2. In the role's **Permissions** tab, add `blender.control` from the Blender MCP
   API.
3. Open **User Management -> Users**, select the person who will authorize
   ChatGPT, and open the user's **Roles** tab.
4. Click **Assign Roles** and assign **Blender Operator**.
5. Confirm that the role appears on the user. A permission attached only to a
   role is not effective until the role is assigned to the user.

## 3. Configure user-delegated application access

Open **Applications -> APIs -> Blender MCP -> Application Access** and set:

- **User-delegated Access:** `Per-app authorization`;
- **Client Access:** `No apps allowed`.

For the default permissions for third-party applications, authorize
user-delegated access to `blender.control`. Leave client access unauthorized;
this deployment uses a user-interactive authorization flow, not a
machine-to-machine grant.

After ChatGPT registers, this page can contain several applications named
ChatGPT with different `tpc_...` client IDs. Verify that the client created by
the current connector shows `1 / 1 permissions granted` for user-delegated
access.

## 4. Configure the ChatGPT MCP client

Use an interactive Authorization Code flow with PKCE. The exact client-registration workflow depends on what the MCP client supports and what is enabled in the Auth0 tenant:

- Prefer a deliberately registered application with exact callback URLs when the client exposes stable registration details.
- Use Dynamic Client Registration only when the MCP client requires it and the tenant has been configured to allow it safely.
- Do not enable password or implicit grants.
- Grant only the Blender MCP API and the `blender.control` permission.

Copy callback URLs exactly from the client connection screen. Do not guess or broaden callback URL patterns.

For a ChatGPT plugin/connector using Dynamic Client Registration (DCR):

1. Select **OAuth** authentication and **Dynamic Client Registration (DCR)**.
2. Under **Scopes**, select `blender.control` as a **Default scope**.
3. Also enter `blender.control` under **Base scopes**. Base scopes are requested
   on every authorization request; selecting only the default scope did not
   cause the tested ChatGPT client to request it reliably.
4. If OpenID support is enabled, keep identity scopes such as `openid` and
   `profile` in the OIDC section. `offline_access` requests refresh tokens.
   `blender.control` is an API authorization scope and does not need to appear
   in the provider's OIDC identity-scope list.

Each DCR attempt can create another third-party Auth0 application. If Auth0
reports that the tenant has reached the entity limit, delete obsolete ChatGPT
`tpc_...` applications from **Applications -> Applications**. Removing an item
from a user's **Authorized Applications** revokes that user's grant but does
not necessarily delete the DCR-created application or recover tenant capacity.

When roles, permissions, or requested scopes change, remove the old ChatGPT
connector and revoke its user grant before creating a fresh connector. This
prevents an old consent grant or refresh-token family from obscuring the new
configuration.

## 5. Configure the server environment

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

## 6. Verify discovery and rejection

After Cloudflare is configured, verify protected-resource metadata:

```bash
curl -i https://blender-mcp.example.com/.well-known/oauth-protected-resource/mcp
```

Expected result: HTTP `200` and JSON metadata describing the protected resource and authorization server.

Verify that the endpoint rejects an unauthenticated request:

```bash
curl -i https://blender-mcp.example.com/mcp
```

Expected result: HTTP `401` with a Bearer challenge. The challenge must include
both the protected-resource metadata URL and the required scope:

```text
resource_metadata="https://blender-mcp.example.com/.well-known/oauth-protected-resource/mcp", scope="blender.control"
```

HTTP `200` is a deployment failure. The project wraps the MCP SDK application
with `OAuthScopeChallengeMiddleware` because MCP Python SDK 2.2.0 did not add
the configured required scopes to this challenge by itself. Recheck this
workaround when upgrading the SDK and keep the regression test until the
upstream behavior has been verified.

## 7. Verify the authorization transaction and token claims

An initial unauthenticated `POST /mcp` returning `401` is expected. It prompts
the client to fetch protected-resource metadata. The expected sequence is:

1. unauthenticated MCP request returns a Bearer challenge;
2. ChatGPT fetches protected-resource metadata;
3. the user authorizes through Auth0;
4. ChatGPT sends an access token to the MCP endpoint.

In Auth0 logs, confirm the event uses the current ChatGPT `client_id`, the exact
Blender MCP audience, and requests `blender.control`. An audience match alone
does not prove authorization. Events showing only `offline_access`, or a null
scope, explain a server rejection for a missing API scope.

For a test access token, inspect claims locally without pasting the token into websites or logs. Confirm:

- `iss` exactly matches `MCP_AUTH_ISSUER`.
- `aud` contains `MCP_AUTH_AUDIENCE`.
- `exp` is in the future.
- `sub` is present.
- `scope` or `permissions` contains `blender.control`.
- the signing algorithm is `RS256` and signature validation uses the tenant JWKS.

The server performs these checks. Decoding a JWT without verifying its signature is diagnostic only and is not authentication.

## 8. Failure-safe rules

- Never disable authentication to troubleshoot Cloudflare.
- Never substitute an ID token for an API access token.
- Never put Auth0 client secrets in `.env.example`, Git, shell history, screenshots, or issue reports.
- Use a distinct Auth0 API for Blender MCP.
- Revoke and rotate a credential immediately if it appears in a log, commit, screenshot, or chat.

## References

- [Auth0 access tokens](https://auth0.com/docs/secure/tokens/access-tokens/get-access-tokens)
- [Auth0 third-party application security controls](https://auth0.com/docs/get-started/applications/third-party-applications/security-controls)
