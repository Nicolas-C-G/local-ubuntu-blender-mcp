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
