# Cloudflare Tunnel

Cloudflare Tunnel publishes the loopback MCP origin without an inbound router, VirtualBox, or firewall rule.

## Required route

| Public hostname | Local service |
| --- | --- |
| `blender-mcp.example.com` | `http://127.0.0.1:8001` |

Never route `127.0.0.1:8765`. That port is the private Blender add-on bridge and uses a different authentication boundary.

## Reuse an existing named tunnel

If the VM already runs the Local Ubuntu MCP tunnel, reuse that named tunnel and add a second public hostname. Do not install a second `cloudflared` system service without a deliberate reason.

In Cloudflare Zero Trust:

1. Open **Networks → Tunnels**.
2. Select the healthy named tunnel for this VM.
3. Add a public hostname such as `blender-mcp.example.com`.
4. Choose service type `HTTP`.
5. Set the URL to `127.0.0.1:8001`.
6. Save and wait for the connector to receive the configuration.

The Ubuntu MCP hostname may continue routing to `127.0.0.1:8000`; the Blender MCP hostname routes separately to `127.0.0.1:8001`.

## New tunnel deployment

If the VM has no named tunnel, create one in Cloudflare Zero Trust, install the connector using Cloudflare's generated command, and protect the tunnel token as a secret.

Confirm service health:

```bash
sudo systemctl status cloudflared --no-pager
sudo journalctl -u cloudflared -n 100 --no-pager
```

Do not paste the generated installation command into public issues because it may contain a tunnel token.

## Transport compatibility

If QUIC/UDP connectivity is blocked but TCP egress is available, configure the connector to use HTTP/2. Keep the known-working IPv4/HTTP2 settings from the Ubuntu MCP tunnel instead of creating a conflicting second service.

The exact configuration mechanism differs between locally managed configuration files and remotely managed token tunnels. Inspect the installed service before changing it:

```bash
systemctl cat cloudflared
sudo cloudflared --version
```

Do not overwrite the service or token file until its current mode is understood and a backup exists.

## Verification

First verify the origin:

```bash
curl -sS -o /tmp/mcp-origin.txt -w '%{http_code}\n' \
  -H 'Host: blender-mcp.example.com' \
  http://127.0.0.1:8001/mcp
```

Expected: `401`.

Then verify the public endpoint:

```bash
curl -i https://blender-mcp.example.com/mcp
curl -i https://blender-mcp.example.com/.well-known/oauth-protected-resource/mcp
```

Expected:

- `/mcp`: `401` without a token.
- protected-resource metadata: `200` with JSON.
- valid publicly trusted TLS certificate.
- no Cloudflare `502` or tunnel error page.

Test that the bridge is not public. There must be no DNS hostname or Cloudflare route for port `8765`.

## Common failures

| Symptom | Likely cause |
| --- | --- |
| `502 Bad Gateway` | MCP service stopped, wrong origin port, or origin connection refused |
| `404` | Wrong hostname route or path |
| `401` on `/mcp` | Expected without an access token |
| Metadata returns HTML | Request reached a Cloudflare error/login page rather than MCP origin |
| Tunnel repeatedly restarts | Missing/invalid tunnel token or incompatible transport settings |
| Works locally, not publicly | DNS/tunnel route, connector health, or outbound network problem |

## References

- [Cloudflare Tunnel documentation](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/)
