# Deployment Automation Status

The repository currently contains application code, a Blender add-on, unit tests, CI, and deployment documentation. Deployment is still manual.

## Current status

| Capability | Status |
| --- | --- |
| Python package installation | Manual commands documented |
| Blender installation/version validation | Manual commands documented |
| Add-on ZIP packaging and installation | Manual commands documented |
| Protected environment creation | Manual commands documented |
| MCP user service | Unit contents documented; unit file not yet shipped |
| Blender protected launcher | Script contents documented; script file not yet shipped |
| Auth0 configuration | Manual dashboard procedure documented |
| Cloudflare hostname route | Manual dashboard procedure documented |
| Local/public verification | Manual commands and checklist documented |
| Mutation toggle | Manual configuration edit documented |
| Upgrade/uninstall | Manual procedure documented |

Documentation is not a substitute for automation. Until the scripts below exist and are tested on a clean VM, deployment should be described as documented but not one-command or fully automated.

## Required deployment scripts

Future work should add and test:

- `scripts/install.sh`
- `scripts/verify-blender.sh`
- `scripts/package-addon.sh`
- `scripts/install-addon.sh`
- `scripts/install-user-service.sh`
- `scripts/install-blender-launcher.sh`
- `scripts/verify-local.sh`
- `scripts/verify-auth.sh`
- `scripts/setup-cloudflare-route.sh`
- `scripts/enable-mutations.sh`
- `scripts/disable-mutations.sh`
- `scripts/uninstall.sh`
- `systemd/blender-control-mcp.service`

## Requirements for automation

Every installer or repair script should:

- fail closed on unsupported OS, missing input, placeholders, weak secrets, and failed verification;
- be safe to run more than once;
- print no secrets;
- preserve unrelated Local Ubuntu MCP configuration;
- reuse an existing Cloudflare named tunnel when selected;
- never publish `8765`;
- leave mutations disabled;
- use explicit target paths and avoid destructive broad deletions;
- run unit tests and local authentication checks before reporting success;
- provide a dry-run or clearly documented confirmation boundary for external changes;
- include rollback or uninstall behavior.

## Release criterion

Mark deployment automation complete only after a fresh Ubuntu Desktop VM can be configured from the repository, passes [Acceptance checklist](ACCEPTANCE_CHECKLIST.md), and the exact tested Ubuntu, Blender, VirtualBox, Python, Auth0, and Cloudflare versions are recorded.
