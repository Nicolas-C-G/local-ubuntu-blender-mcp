# Deployment on Ubuntu Desktop in VirtualBox

This is the canonical, end-to-end deployment procedure for the current MVP. It deploys the MCP server and the custom constrained Blender bridge in one Ubuntu Desktop VM.

The procedure is intentionally manual while deployment scripts are being developed. Complete every verification gate before moving to the next phase.

## 1. Deployment result

At the end of this guide:

- Blender runs in the logged-in Ubuntu desktop session.
- The custom add-on listens only on `127.0.0.1:8765`.
- The MCP server listens only on `127.0.0.1:8001`.
- Auth0 validates public access tokens and the `blender.control` scope.
- A Cloudflare named tunnel exposes only the MCP server through HTTPS.
- Mutating tools remain disabled until the acceptance checklist passes.

## 2. Prerequisites

- VirtualBox 7.x on the host.
- A supported Ubuntu Desktop VM with current security updates.
- A non-root Ubuntu user with `sudo` access.
- Blender 5.1+ for the initial deployment-validation baseline.
- A public DNS zone managed by Cloudflare.
- An Auth0 tenant.
- The repository and feature branch containing the MVP.

Review [VirtualBox preparation](VIRTUALBOX.md) before installing software.

## 3. Prepare Ubuntu

Open a terminal inside the VM:

```bash
sudo apt update
sudo apt upgrade
sudo apt install git python3 python3-venv python3-pip curl jq zip ca-certificates
```

Restart after a kernel or graphics-stack update:

```bash
sudo reboot
```

Do not add VirtualBox NAT port-forwarding rules for ports `8001` or `8765`.

## 4. Install and verify Blender

Follow [Blender and add-on installation](BLENDER_INSTALLATION.md). Do not continue until both commands succeed:

```bash
blender --version
ss -ltn | grep ':8765'
```

The second command succeeds only after Blender is launched through the protected wrapper and the add-on is enabled.

## 5. Install the MCP package

Choose a stable installation directory owned by the desktop user. The examples use `$HOME/apps`:

```bash
mkdir -p "$HOME/apps"
cd "$HOME/apps"
git clone https://github.com/Nicolas-C-G/local-ubuntu-blender-mcp.git
cd local-ubuntu-blender-mcp
git checkout feature/blender-mcp-mvp
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install .
.venv/bin/python -m unittest discover -s tests -v
```

For deployment, use `pip install .` rather than editable mode. Record the commit being deployed:

```bash
git rev-parse --verify HEAD
```

## 6. Create protected configuration

```bash
mkdir -p "$HOME/.config/blender-mcp" "$HOME/.local/state/blender-mcp"
cp .env.example "$HOME/.config/blender-mcp/env"
chmod 700 "$HOME/.config/blender-mcp" "$HOME/.local/state/blender-mcp"
chmod 600 "$HOME/.config/blender-mcp/env"
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
```

Copy the generated secret. Edit the protected environment file:

```bash
nano "$HOME/.config/blender-mcp/env"
```

Set all example values. Use an absolute audit path, replacing `YOUR_USERNAME`:

```dotenv
MCP_PUBLIC_HOSTNAME=blender-mcp.example.com
MCP_PUBLIC_BASE_URL=https://blender-mcp.example.com
MCP_RESOURCE_URL=https://blender-mcp.example.com/mcp
MCP_AUTH_AUDIENCE=https://blender-mcp.example.com/mcp
MCP_AUTH_ISSUER=https://YOUR_AUTH0_TENANT.auth0.com/
MCP_AUTH_REQUIRED_SCOPES=blender.control
BLENDER_MCP_PORT=8001
MCP_ALLOWED_HOSTS=127.0.0.1,127.0.0.1:*,localhost,localhost:*,blender-mcp.example.com
MCP_ALLOWED_ORIGINS=https://blender-mcp.example.com
BLENDER_BRIDGE_URL=http://127.0.0.1:8765/command
BLENDER_BRIDGE_PORT=8765
BLENDER_BRIDGE_TIMEOUT=12
BLENDER_BRIDGE_TOKEN=PASTE_THE_GENERATED_SECRET
BLENDER_MCP_ENABLE_MUTATIONS=false
BLENDER_MCP_AUDIT_LOG=/home/YOUR_USERNAME/.local/state/blender-mcp/audit.jsonl
```

Do not use shell expressions, `$HOME`, or `~` in this file. systemd environment files do not perform normal shell expansion.

Verify that no placeholder remains:

```bash
if grep -Eq 'example\.com|YOUR_|REPLACE_|PASTE_' "$HOME/.config/blender-mcp/env"; then
  echo 'ERROR: configuration still contains placeholders'
else
  echo 'Configuration placeholders cleared'
fi
```

## 7. Configure Auth0

Follow [Auth0 configuration](AUTH0.md). The following values must agree exactly:

- Auth0 API Identifier
- `MCP_RESOURCE_URL`
- `MCP_AUTH_AUDIENCE`
- the audience requested by the MCP client

Keep the trailing slash in `MCP_AUTH_ISSUER`.

## 8. Create the user service

The MCP server belongs to the logged-in desktop user because Blender also runs in that interactive session.

```bash
mkdir -p "$HOME/.config/systemd/user"
nano "$HOME/.config/systemd/user/blender-control-mcp.service"
```

Paste the following, replacing `YOUR_USERNAME` in both paths:

```ini
[Unit]
Description=Constrained Blender MCP server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=/home/YOUR_USERNAME/.config/blender-mcp/env
ExecStart=/home/YOUR_USERNAME/apps/local-ubuntu-blender-mcp/.venv/bin/blender-control-mcp
WorkingDirectory=/home/YOUR_USERNAME/apps/local-ubuntu-blender-mcp
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/home/YOUR_USERNAME/.local/state/blender-mcp

[Install]
WantedBy=default.target
```

Load and start it:

```bash
systemctl --user daemon-reload
systemctl --user enable --now blender-control-mcp.service
systemctl --user status blender-control-mcp.service --no-pager
```

If the service fails, read [Troubleshooting](TROUBLESHOOTING.md) before weakening its security settings.

## 9. Verify the local OAuth boundary

```bash
curl -sS -o /tmp/blender-mcp-local.txt -w '%{http_code}\n' \
  -H 'Host: blender-mcp.example.com' \
  http://127.0.0.1:8001/mcp
cat /tmp/blender-mcp-local.txt
```

Expected result: HTTP `401`, not `200`.

Check listeners:

```bash
ss -ltnp | grep -E ':(8001|8765)\b'
```

Both listeners must show `127.0.0.1`. A listener on `0.0.0.0`, the VM address, or `[::]` is a failed security gate.

## 10. Start Blender securely

Use the wrapper created in [Blender and add-on installation](BLENDER_INSTALLATION.md):

```bash
blender-mcp-session
```

Keep Blender open. Confirm the bridge answers only with its secret. A request without a token must be rejected:

```bash
curl -sS -o /tmp/blender-bridge.txt -w '%{http_code}\n' \
  -H 'Content-Type: application/json' \
  --data '{"action":"health","arguments":{}}' \
  http://127.0.0.1:8765/command
cat /tmp/blender-bridge.txt
```

Expected result: HTTP `401`.

## 11. Configure Cloudflare

Follow [Cloudflare Tunnel](CLOUDFLARE.md). Reuse the existing Ubuntu MCP named tunnel when appropriate, but give Blender MCP a separate hostname that routes to `http://127.0.0.1:8001`.

Never create a public route to `8765`.

## 12. Connect the MCP client

Use this public endpoint in the MCP client:

```text
https://blender-mcp.example.com/mcp
```

Complete the Auth0 authorization flow and grant only the `blender.control` permission. First call `blender_health`, then the read-only tools.

Do not enable mutations yet.

## 13. Acceptance gate

Complete [Acceptance checklist](ACCEPTANCE_CHECKLIST.md). Enable mutations only for a supervised editing session after every critical check passes.

## 14. Deployment record

Record, without secrets:

- VM Ubuntu version.
- VirtualBox version.
- Blender version and installation source.
- repository commit SHA.
- public hostname.
- Auth0 tenant domain and API Identifier.
- Cloudflare tunnel name or ID.
- date and acceptance-checklist result.

Never record access tokens, the bridge token, tunnel tokens, client secrets, or private keys.
