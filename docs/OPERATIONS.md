# Operations

## Normal start sequence

1. Log in to the Ubuntu desktop.
2. Confirm mutations are disabled.
3. Start or verify the MCP user service.
4. Launch Blender through `blender-mcp-session`.
5. Confirm the add-on is enabled and port `8765` is loopback-only.
6. Call `blender_health`, then a read-only tool.

```bash
grep '^BLENDER_MCP_ENABLE_MUTATIONS=' "$HOME/.config/blender-mcp/env"
systemctl --user start blender-control-mcp.service
systemctl --user status blender-control-mcp.service --no-pager
blender-mcp-session
```

## Stop sequence

1. End the MCP client session.
2. Disable mutations if they were enabled.
3. Stop the MCP service.
4. Save intentionally in Blender, then close Blender.

```bash
systemctl --user stop blender-control-mcp.service
```

The Cloudflare tunnel may remain running for other services; authentication still protects the stopped Blender MCP origin.

## Logs

MCP service:

```bash
journalctl --user -u blender-control-mcp.service -n 100 --no-pager
journalctl --user -u blender-control-mcp.service -f
```

Cloudflare connector:

```bash
sudo journalctl -u cloudflared -n 100 --no-pager
```

Audit records:

```bash
tail -n 50 "$HOME/.local/state/blender-mcp/audit.jsonl"
```

Audit logs may contain object names and transform arguments. Protect them as operational data even though they must not contain access tokens.

## Enable mutations temporarily

Mutations are a server-side policy, not a client preference.

1. Stop the service.
2. Back up the active `.blend` file or create a Blender checkpoint.
3. Edit the protected environment file.
4. Set `BLENDER_MCP_ENABLE_MUTATIONS=true`.
5. Restart the service.
6. Perform only supervised changes.
7. Return the setting to `false` and restart immediately afterward.

```bash
systemctl --user stop blender-control-mcp.service
nano "$HOME/.config/blender-mcp/env"
systemctl --user start blender-control-mcp.service
```

Verify the effective setting by attempting an intentionally safe, confirmed operation—not by assuming a file edit was loaded.

## Rotate the bridge token

1. Stop the MCP service and close Blender.
2. Generate a new secret.
3. Replace `BLENDER_BRIDGE_TOKEN` in the protected environment file.
4. Confirm file mode `600`.
5. Restart the MCP service.
6. Launch Blender through the protected wrapper.
7. Run the acceptance checks.

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
chmod 600 "$HOME/.config/blender-mcp/env"
```

The MCP service and Blender must use the same new value. Do not pass the token as a command-line argument.

## Update the application

Keep mutations disabled and deploy only a reviewed commit:

```bash
cd "$HOME/apps/local-ubuntu-blender-mcp"
git status --short
git fetch --prune origin
git checkout feature/blender-mcp-mvp
git pull --ff-only
.venv/bin/python -m pip install .
.venv/bin/python -m unittest discover -s tests -v
systemctl --user restart blender-control-mcp.service
```

Repackage and reinstall the add-on if `blender_addon/` changed. Record the new commit SHA and repeat the acceptance checklist.

Do not discard local changes automatically. Investigate a dirty working tree before updating.

## Backups

Back up:

- `.blend` files needed by the project.
- non-secret deployment records.
- reviewed configuration structure without values.

Do not distribute VM snapshots containing active bridge, Auth0, or Cloudflare credentials.

## Uninstall

1. Disable mutations and stop the user service.
2. Disable and remove the Blender add-on.
3. Disable the user service.
4. Remove the Blender public hostname from the tunnel.
5. Revoke the dedicated Auth0 client/grants if no longer needed.
6. Archive audit records according to your retention needs.
7. Remove application files only after confirming no unrelated data is inside the directory.

```bash
systemctl --user disable --now blender-control-mcp.service
```

The current repository does not include an automated uninstall script. Do not delete the shared Cloudflare tunnel when it also serves Ubuntu MCP.
