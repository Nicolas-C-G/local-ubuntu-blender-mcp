# Blender and Custom Add-on Installation

This project does not use Blender's official Lab MCP add-on. It installs a custom add-on that exposes only a small allowlist of operations over a token-authenticated loopback bridge.

## Supported deployment baseline

The source advertises Blender 4.2 compatibility through `bl_info`. The initial deployment-validation baseline is Blender 5.1 or newer. Do not describe a Blender version as supported until the acceptance checklist and real-Blender integration test pass on that version.

## Install Blender

Prefer the official Linux archive from Blender rather than assuming the Ubuntu repository contains the required version.

1. Download the Linux archive from <https://www.blender.org/download/> inside the VM.
2. Verify the download came from an official Blender domain.
3. Extract it to a versioned directory under `/opt`.

Example, after adjusting the filename and directory to the downloaded version:

```bash
cd "$HOME/Downloads"
tar -tf blender-*-linux-x64.tar.xz | head
sudo mkdir -p /opt/blender
sudo tar -xJf blender-*-linux-x64.tar.xz --strip-components=1 -C /opt/blender
sudo ln -sfn /opt/blender/blender /usr/local/bin/blender
blender --version
```

Inspect the archive before extracting it. The wildcard must identify exactly one intended Blender archive.

## Package the custom add-on

From the repository root:

```bash
rm -f /tmp/blender_mcp_bridge.zip
cd blender_addon
zip -r /tmp/blender_mcp_bridge.zip blender_mcp_bridge -x '*/__pycache__/*' '*.pyc'
unzip -l /tmp/blender_mcp_bridge.zip
cd ..
```

The archive root must contain `blender_mcp_bridge/`, and that directory must contain `__init__.py`.

## Install the add-on

The exact menu wording varies slightly by Blender release:

1. Open Blender.
2. Open **Edit → Preferences**.
3. Open **Add-ons** or **Extensions**.
4. Choose **Install from Disk**.
5. Select `/tmp/blender_mcp_bridge.zip`.
6. Enable **Blender MCP Bridge**.

If the add-on was enabled before the bridge environment existed, disable it, close Blender completely, launch Blender through the wrapper below, and enable it again.

## Create the protected Blender launcher

The desktop application normally does not inherit variables from a terminal or systemd service. Use one protected environment file for both Blender and the MCP process.

```bash
mkdir -p "$HOME/.local/bin"
nano "$HOME/.local/bin/blender-mcp-session"
```

Paste:

```bash
#!/usr/bin/env bash
set -euo pipefail
environment_file="$HOME/.config/blender-mcp/env"
if [[ ! -r "$environment_file" ]]; then
  echo "Missing protected environment file: $environment_file" >&2
  exit 1
fi
set -a
# shellcheck disable=SC1090
source "$environment_file"
set +a
exec /usr/local/bin/blender "$@"
```

Then:

```bash
chmod 700 "$HOME/.local/bin/blender-mcp-session"
```

Make sure `$HOME/.local/bin` is in the user's `PATH`, or invoke the wrapper with its absolute path.

## Start and verify the bridge

```bash
blender-mcp-session
```

After Blender opens and the add-on is enabled:

```bash
ss -ltnp | grep ':8765'
```

The address must be `127.0.0.1:8765`.

Verify that an unauthenticated command is rejected:

```bash
curl -sS -o /tmp/bridge-response.json -w '%{http_code}\n' \
  -H 'Content-Type: application/json' \
  --data '{"action":"health","arguments":{}}' \
  http://127.0.0.1:8765/command
cat /tmp/bridge-response.json
```

Expected result: HTTP `401`.

## Update the add-on

1. Keep mutations disabled.
2. Pull the reviewed repository update.
3. Recreate the ZIP.
4. Disable the old add-on.
5. Install the new ZIP and re-enable it.
6. Restart Blender through the protected launcher.
7. Repeat the local bridge and read-only acceptance tests.

## Remove the add-on

Disable and remove **Blender MCP Bridge** from Blender Preferences. Close Blender and verify nothing listens on `8765`:

```bash
ss -ltn | grep ':8765' || echo 'Bridge is not listening'
```
