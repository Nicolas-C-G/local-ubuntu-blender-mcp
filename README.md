# Local Ubuntu Blender MCP

A secure, OAuth-protected Model Context Protocol server that lets ChatGPT inspect and make constrained changes to Blender running on an Ubuntu desktop.

This is an independent, security-focused implementation inspired by the Local Ubuntu MCP architecture. It is not Blender's official Lab MCP server. In particular, it does not expose arbitrary Python or shell execution.

> [!WARNING]
> This software controls an interactive Blender session. Keep mutations disabled until authentication, bridge isolation, and audit logging have been verified.

## Capabilities

| MCP tool | Default | Purpose |
| --- | --- | --- |
| `blender_health` | Enabled | Check Blender version, current file, and bridge status |
| `blender_get_scene` | Enabled | Read scene, frame, renderer, camera, selection, and object count |
| `blender_list_objects` | Enabled | List up to 200 scene objects and their transforms |
| `blender_get_object` | Enabled | Inspect one named object |
| `blender_turntable_start` | Enabled | Start a 360° viewport capture of a geometry object |
| `blender_turntable_status` | Enabled | Read capture progress and errors |
| `blender_turntable_sheet` | Enabled | Return the completed contact sheet as an MCP image |
| `blender_create_primitive` | Disabled | Create an allowlisted mesh primitive |
| `blender_create_collection` | Disabled | Create a collection and move existing scene objects into it |
| `blender_set_transform` | Disabled | Replace one object's location, rotation, and scale |

Supported primitives are `CUBE`, `UV_SPHERE`, `CYLINDER`, `CONE`, `TORUS`, and `PLANE`. Rotation values are radians.

### 360° viewport preview

With Blender open on a desktop and a 3D View visible, call `blender_turntable_start`
with a geometry object or collection name and `views` of 8, 12 (default), 16, or 24. Poll
`blender_turntable_status(job_id)` until `state` is `completed`, then call
`blender_turntable_sheet(job_id)` to see a labeled PNG contact sheet in the MCP
client. Failed jobs report an error in the status response. Captures expire after
10 minutes; download the sheet before then. Only one capture can run at a time.
The tool rotates the viewport around the selected object's bounding box or the
combined bounds of the collection's geometry objects (including child collections); it
does not rotate or save the model. It restores the original viewport and render
settings after each frame. Other scene objects are hidden temporarily for each
frame so the sheet focuses on the named object or collection. The scene is temporarily blocked from changes by
this bridge while capture is running. Other manual edits in Blender during a
capture may still affect the result, so leave the scene idle until it finishes.

The 3D View must remain open for capture. The bridge serves each 320 px PNG
over its token-authenticated loopback interface; the MCP server assembles them
into one 1280 px wide sheet. Images never go through the bridge JSON response.
This is a turntable around the world's vertical axis, not a full spherical view;
top and bottom views are future extensions. Verify the first capture against
the visible Blender scene in the target VM before relying on the preview.

### Collections

For example, `blender_create_collection(name="RobotArm", object_names=["Cube", "RobotArm_Pedestal"])` creates a new scene collection and moves both existing objects into it. The tool accepts 1 to 200 distinct names. It checks every object before changing the scene, rejects an existing collection name, and preserves object links in other scenes. Enable mutations to use it. Save the `.blend` file in Blender to keep the change across sessions.

## Architecture

```mermaid
flowchart TD
    A["ChatGPT"] -->|"OAuth and HTTPS"| B["Cloudflare Tunnel"]
    B --> C["Blender MCP :8001"]
    C -->|"Token-authenticated loopback"| D["Blender bridge :8765"]
    D -->|"Main-thread queue"| E["Blender Python API"]
```

There are two authentication boundaries:

1. Auth0 protects the public MCP resource with the `blender.control` scope.
2. A separate high-entropy secret protects the loopback bridge between the MCP process and Blender.

The bridge accepts only named, validated operations and binds to `127.0.0.1`. Never publish port `8765` through Cloudflare, a firewall rule, VirtualBox port forwarding, or a router.

## Deployment documentation

Start with [Deployment on Ubuntu Desktop in VirtualBox](docs/DEPLOYMENT_UBUNTU_VIRTUALBOX.md).

| Document | Purpose |
| --- | --- |
| [VirtualBox preparation](docs/VIRTUALBOX.md) | VM sizing, NAT, graphics, snapshots, and Guest Additions |
| [Blender and add-on installation](docs/BLENDER_INSTALLATION.md) | Install Blender and the custom bridge |
| [Auth0 configuration](docs/AUTH0.md) | API, audience, scope, issuer, and login setup |
| [Cloudflare Tunnel](docs/CLOUDFLARE.md) | Add a dedicated hostname to a new or existing named tunnel |
| [Operations](docs/OPERATIONS.md) | Start, stop, update, rotate secrets, and control mutations |
| [Security model](docs/SECURITY.md) | Trust boundaries, controls, and residual risks |
| [Troubleshooting](docs/TROUBLESHOOTING.md) | Common VM, Blender, OAuth, tunnel, and port failures |
| [Acceptance checklist](docs/ACCEPTANCE_CHECKLIST.md) | Evidence required before enabling mutations |
| [Automation status](docs/AUTOMATION_STATUS.md) | Manual steps and deployment scripts still to be implemented |

## Development setup

Requirements:

- Ubuntu Desktop
- Blender 4.2+ at code level; Blender 5.1+ is the initial deployment-validation baseline
- Python 3.11+
- An Auth0 API for the public MCP resource
- A Cloudflare named tunnel for remote ChatGPT access

```bash
git clone https://github.com/Nicolas-C-G/local-ubuntu-blender-mcp.git
cd local-ubuntu-blender-mcp
git checkout feature/blender-mcp-mvp

python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e .
.venv/bin/python -m pip install pytest
```

For production-like deployment, do not continue from this abbreviated section; follow the complete deployment guide.

## Tests

```bash
.venv/bin/python -m pytest
```

CI installs the package, compiles the add-on source, and runs the unit tests. A real-Blender integration test inside the target VM is still required before a release is described as deployment-validated.

## Current limitations

- Blender must remain open with the custom add-on enabled.
- Saving, deleting, final camera rendering, materials, modifiers, undo checkpoints, and file export are intentionally deferred.
- Deployment is currently documented as a manual procedure; install and verification shell scripts are not yet present.
- The `.mcpb` bundle mentioned by Blender's official Lab project is not used by this remote OAuth/Cloudflare architecture.

## License

MIT
