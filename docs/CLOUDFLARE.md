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

If the VM has no named tunnel, create a remotely managed tunnel and connect
this VM to it.

Before creating the tunnel, confirm that the local MCP origin is running:

```bash
curl -sS -o /tmp/mcp-origin.txt -w '%{http_code}\n' \
  -H 'Host: blender-mcp.example.com' \
  http://127.0.0.1:8001/mcp
```

The expected status is `401`. Fix the local MCP service before continuing if
the command cannot connect or returns another status.

Check whether a `cloudflared` service is already installed:

```bash
if systemctl cat cloudflared >/dev/null 2>&1; then
  echo "An existing cloudflared service was found; use the reuse procedure above"
else
  echo "No cloudflared service was found; continue with the new tunnel procedure"
fi
```

Do not install a second connector service if an existing service is found.

Create and install the tunnel:

1. Sign in to the Cloudflare dashboard for the account that manages your
   domain.
2. Open **Networking → Tunnels** and select **Create tunnel**.
3. Enter a descriptive name, such as `blender-mcp-vm`, and select
   **Create tunnel**.
4. Under **Setup environment**, select **Linux** and the architecture that
   matches the VM. For a typical 64-bit Intel or AMD VirtualBox VM, choose
   **64-bit**.
5. Copy the complete command shown under **Install and run a connector**.
6. Run that generated command once in the Ubuntu VM terminal. It installs
   `cloudflared`, registers the connector as a system service, and includes
   the tunnel token required by this tunnel.
7. Return to Cloudflare and wait until the connector reports **Connected** or
   the tunnel reports **Healthy**, then select **Continue**.

Do not copy a command from this guide or type the tunnel token manually. Use
the command generated for this specific tunnel.

Add the public route:

1. Open the new tunnel and select the **Routes** tab.
2. Select **Add route → Published application**.
3. Under **Hostname**, enter the chosen subdomain, such as `blender-mcp`,
   and select the domain managed by Cloudflare.
4. Leave **Path** empty so OAuth discovery and the MCP endpoint use the same
   hostname.
5. Set **Service URL** to `http://127.0.0.1:8001`.
6. Select **Add route** or **Save**.
7. Wait for the tunnel to report **Healthy**. Cloudflare creates the DNS route
   for the published hostname as part of this procedure.

Never create a route to `127.0.0.1:8765`; that is the private Blender bridge.

Confirm service health:

```bash
sudo systemctl status cloudflared --no-pager
sudo journalctl -u cloudflared -n 100 --no-pager
```

The service status should be `active (running)`, and its logs should show
successful connections to Cloudflare.

Do not paste the generated installation command into public issues, chat
messages, screenshots, or documentation because it contains the tunnel token.
Treat that command and token as secrets.

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
