# Troubleshooting

Troubleshoot from the inside out: Blender bridge, local MCP, Auth0 metadata, Cloudflare, then the MCP client. Do not disable security controls to make an error disappear.

## Diagnostic bundle

These commands avoid printing configured secrets:

```bash
blender --version
python3 --version
systemctl --user status blender-control-mcp.service --no-pager
journalctl --user -u blender-control-mcp.service -n 100 --no-pager
sudo systemctl status cloudflared --no-pager
ss -ltnp | grep -E ':(8001|8765)\b'
curl -sS -o /dev/null -w 'local MCP: %{http_code}\n' \
  -H 'Host: blender-mcp.example.com' http://127.0.0.1:8001/mcp
curl -sS -o /dev/null -w 'public MCP: %{http_code}\n' \
  https://blender-mcp.example.com/mcp
```

Redact usernames, hostnames if necessary, object names, and all credential values before sharing output.

## Blender bridge is not listening on 8765

Check:

- Blender is open.
- **Blender MCP Bridge** is installed and enabled.
- Blender was launched through `blender-mcp-session`.
- `BLENDER_BRIDGE_TOKEN` is present and at least 32 characters.
- another process is not already using the port.

```bash
ss -ltnp | grep ':8765'
```

Close all Blender instances before relaunching. Enabling the add-on in an already-running process does not retroactively provide missing environment variables.

## MCP service fails immediately

Inspect logs:

```bash
journalctl --user -u blender-control-mcp.service -n 100 --no-pager
```

Common causes:

- missing `MCP_AUTH_ISSUER` or `MCP_RESOURCE_URL`;
- placeholder tenant or hostname;
- invalid bridge token length;
- audit path blocked by service hardening;
- wrong absolute `ExecStart` path;
- Python dependencies not installed in the selected virtual environment.

Validate the unit and reload after edits:

```bash
systemd-analyze --user verify "$HOME/.config/systemd/user/blender-control-mcp.service"
systemctl --user daemon-reload
systemctl --user restart blender-control-mcp.service
```

## Source changes are not loaded

Check the module path used by the service's virtual environment:

```bash
cd "$HOME/apps/local-ubuntu-blender-mcp"
.venv/bin/python - <<'PY'
import blender_mcp.auth
print(blender_mcp.auth.__file__)
PY
```

If it points into `.venv/.../site-packages`, `pip install .` created a fixed
copy. Reinstall after source changes:

```bash
.venv/bin/python -m pip install --force-reinstall --no-deps .
systemctl --user restart blender-control-mcp.service
```

During active development, install the project in editable mode instead:

```bash
.venv/bin/python -m pip install --no-deps --editable .
```

Do not mix this diagnosis with OAuth configuration: stale installed code can
make newly added logging or fixes appear ineffective even when Auth0 is
configured correctly.

## Port already in use

```bash
ss -ltnp | grep -E ':(8001|8765)\b'
```

Port `8000` belongs to Local Ubuntu MCP in the shared design. Blender MCP intentionally uses `8001`. Do not kill an unidentified process or change ports until its owner is understood.

If changing `8765`, update both `BLENDER_BRIDGE_PORT` and `BLENDER_BRIDGE_URL` consistently and restart Blender and the MCP service.

## Local MCP returns 400 or 421 instead of 401

The DNS-rebinding protection probably rejected the `Host` header. Ensure `MCP_ALLOWED_HOSTS` contains the exact public hostname and local forms, then restart the service.

Use the public host during the local check:

```bash
curl -i -H 'Host: blender-mcp.example.com' http://127.0.0.1:8001/mcp
```

## Local MCP works but Cloudflare returns 502

- Verify the tunnel route uses `http://127.0.0.1:8001`.
- Verify the connector runs inside the same VM as the origin.
- Check `cloudflared` logs.
- Confirm the route was added to the intended named tunnel.
- Confirm no second connector service is fighting for an invalid or missing token.

```bash
sudo journalctl -u cloudflared -n 100 --no-pager
```

## Cloudflare connector repeatedly restarts

Inspect its unit before editing:

```bash
systemctl cat cloudflared
sudo journalctl -u cloudflared -n 100 --no-pager
```

A missing token file, invalid token, or blocked QUIC transport are common causes. If the Ubuntu MCP tunnel already uses HTTP/2 over IPv4 successfully, preserve that configuration and add the Blender hostname to the same tunnel.

## OAuth discovery fails

```bash
curl -i https://blender-mcp.example.com/.well-known/oauth-protected-resource/mcp
```

The response must be JSON from the MCP service, not an HTML Cloudflare error page. Verify the public base URL, resource URL, issuer, Host allowlist, and tunnel route.

## Bearer challenge does not advertise `blender.control`

Inspect the public challenge:

```bash
curl -sS -D - -o /dev/null https://blender-mcp.example.com/mcp \
  | grep -i '^www-authenticate:'
```

It must contain both `resource_metadata="..."` and
`scope="blender.control"`. MCP Python SDK 2.2.0 did not include the required
scope in this header by itself; this project adds it with
`OAuthScopeChallengeMiddleware`. Confirm that the running installation contains
that middleware and that the service was reinstalled/restarted after the
change.

The first request without a bearer token returning `401` is expected OAuth
discovery behavior. A bearer token being rejected afterward is a separate
token-validation or authorization failure.

## Access token is rejected

Compare exact claims and configuration:

- issuer, including trailing slash;
- audience, including `/mcp`;
- unexpired `exp`;
- `blender.control` in `scope` or `permissions`;
- Auth0 `RS256` signing and reachable JWKS;
- system clock synchronized inside the VM.

```bash
timedatectl status
```

Do not paste the token into an online decoder.

The verifier's diagnostic messages separate common failures:

- `rejected during JWT validation`: signature, issuer, audience, expiry, token
  format, or JWKS validation failed;
- `audience mismatch`: the resource identifier differs;
- `missing scopes ['blender.control']`: the token is valid for the audience but
  the API permission was not requested or granted.

Never add logging that prints the bearer token itself.

## Token is valid but `blender.control` is missing

Check all four authorization layers; each is required:

1. ChatGPT has `blender.control` selected as a **Default scope** and entered as
   a **Base scope**.
2. Auth0 API RBAC and **Add Permissions in the Access Token** are enabled.
3. The `Blender Operator` role contains the API permission.
4. The role is assigned to the actual user authorizing the connector.

Also inspect **Applications -> APIs -> Blender MCP -> Application Access** and
confirm that the current ChatGPT `tpc_...` application has `1 / 1` delegated
permissions. In Auth0 events, a correct audience with `scope: null` or only
`scope: offline_access` is not sufficient.

After correcting these settings, remove the old ChatGPT connector, revoke its
entry under the user's **Authorized Applications**, and create a fresh
connector so the authorization and consent flow runs again.

## Dynamic Client Registration reaches the tenant limit

Repeated connector creation can leave multiple third-party ChatGPT applications
with different `tpc_...` IDs. Delete only obsolete ChatGPT clients under
**Applications -> Applications**. Keep the client ID associated with the active
connector.

Deleting a user's **Authorized Applications** entry revokes consent but does
not necessarily delete the registered client or reduce the tenant's application
count. These are different cleanup operations.

## Request immediately after restart cannot connect

`systemctl restart` can return before Uvicorn has opened port `8001`. Check the
service and wait for its listener:

```bash
systemctl --user status blender-control-mcp.service --no-pager -l
for attempt in $(seq 1 30); do
  ss -ltn | grep -q '127.0.0.1:8001' && break
  sleep 1
done
ss -ltn | grep '127.0.0.1:8001'
```

If the listener never appears, inspect the full service journal. HTTP `000`
during this startup window does not diagnose an OAuth problem.

## Read-only tools work but mutation tools fail

This is expected when `BLENDER_MCP_ENABLE_MUTATIONS=false`. Follow the supervised mutation procedure in [Operations](OPERATIONS.md). Restart the service after changing the file.

## MCP can reach Blender but operations time out

- Keep Blender open and responsive.
- Exit modal dialogs and long-running renders.
- Check whether the UI is blocked by an operation.
- Inspect the MCP audit log.
- Start with `blender_health`.

Do not increase `BLENDER_BRIDGE_TIMEOUT` indefinitely; first determine why Blender's main thread is unavailable.

## Blender viewport or startup crashes in VirtualBox

This is usually a VM graphics problem rather than MCP authentication. Update VirtualBox Guest Additions, verify the VMSVGA controller, and compare behavior with 3D acceleration temporarily disabled. Test a clean Blender launch without the custom add-on to isolate the layer.

## Audit log is missing

Check the absolute path and parent permissions:

```bash
grep '^BLENDER_MCP_AUDIT_LOG=' "$HOME/.config/blender-mcp/env"
ls -ld "$HOME/.local/state/blender-mcp"
```

The current application does not stop operations when audit writing fails. Treat a missing audit log as a failed acceptance gate and fix it before enabling mutations.
