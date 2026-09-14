# Local Ubuntu Blender MCP

A secure, OAuth-protected Model Context Protocol server that lets ChatGPT inspect and make constrained changes to Blender running on an Ubuntu desktop.

This project reuses the security architecture proven in Local Ubuntu MCP while remaining an independent repository and deployment.

> [!WARNING]
> This software controls an interactive Blender session. Keep mutations disabled until authentication, bridge isolation, and audit logging have been verified. The project deliberately provides no arbitrary Python or shell execution tool.

## MVP capabilities

| MCP tool | Default | Purpose |
| --- | --- | --- |
| blender_health | Enabled | Check the Blender version, current file, and bridge status |
| blender_get_scene | Enabled | Read scene, frame, renderer, camera, selection, and object count |
| blender_list_objects | Enabled | List up to 200 scene objects and their transforms |
| blender_get_object | Enabled | Inspect one named object |
| blender_create_primitive | Disabled | Create an allowlisted mesh primitive |
| blender_set_transform | Disabled | Replace one object's location, rotation, and scale |

Supported primitives are CUBE, UV_SPHERE, CYLINDER, CONE, TORUS, and PLANE. Rotation values are radians.

## Architecture

~~~mermaid
flowchart TD
    A["ChatGPT"] -->|"OAuth and HTTPS"| B["Cloudflare Tunnel"]
    B --> C["Blender MCP :8001"]
    C -->|"Token-authenticated loopback"| D["Blender bridge :8765"]
    D -->|"Main-thread queue"| E["Blender Python API"]
~~~

There are two authentication boundaries:

1. Auth0 protects the public MCP resource with the blender.control scope.
2. A separate high-entropy secret protects the loopback bridge between the MCP process and Blender.

The bridge accepts only named, validated operations and binds to 127.0.0.1. Never publish port 8765 through Cloudflare, a firewall rule, or router forwarding.

## Repository layout

~~~text
.
├── blender_addon/blender_mcp_bridge/   Blender-side bridge
├── src/blender_mcp/                    MCP server, OAuth, validation, audit
├── tests/                              Unit tests
├── .env.example                        Configuration template
└── pyproject.toml                      Python package metadata
~~~

## Development setup

Requirements:

- Ubuntu Desktop
- Blender 4.2 or newer
- Python 3.11 or newer
- An Auth0 API configured for the public MCP resource
- Cloudflare Tunnel for remote ChatGPT access

Clone and install the MCP package:

~~~bash
git clone https://github.com/Nicolas-C-G/local-ubuntu-blender-mcp.git
cd local-ubuntu-blender-mcp
git checkout develop

python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e .
~~~

Create the local configuration:

~~~bash
cp .env.example .env
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
nano .env
chmod 600 .env
~~~

Put the generated value in BLENDER_BRIDGE_TOKEN. Use the same local .env when launching Blender and the MCP server. Never commit this file or token.

For a development audit path that needs no system configuration, set:

~~~bash
BLENDER_MCP_AUDIT_LOG=/tmp/blender-control-mcp-audit.jsonl
~~~

## Install the Blender add-on

Create a ZIP archive:

~~~bash
cd blender_addon
zip -r blender_mcp_bridge.zip blender_mcp_bridge
cd ..
~~~

In Blender:

1. Open Edit > Preferences > Add-ons.
2. Choose Install from Disk.
3. Select blender_addon/blender_mcp_bridge.zip.
4. Enable Blender MCP Bridge.

The bridge reads BLENDER_BRIDGE_TOKEN and BLENDER_BRIDGE_PORT when the add-on is enabled. Launch Blender from a terminal containing the environment:

~~~bash
set -a
source .env
set +a
blender
~~~

If Blender was already open without the environment, close it completely and launch it again before enabling the add-on.

## Start the MCP server

In a second terminal:

~~~bash
set -a
source .env
set +a
.venv/bin/blender-control-mcp
~~~

The MCP endpoint listens only on:

~~~text
http://127.0.0.1:8001/mcp
~~~

Port 8001 intentionally avoids a conflict with Local Ubuntu MCP on port 8000. An unauthenticated request must return HTTP 401.

## Auth0 and Cloudflare

Create a distinct Auth0 API for this service:

- Identifier: the exact MCP_RESOURCE_URL
- Signing algorithm: RS256
- Permission: blender.control
- Dynamic Client Registration: enabled for the ChatGPT connection

Create a separate Cloudflare hostname, for example blender-mcp.example.com, and route it to http://127.0.0.1:8001. Do not route the Blender bridge port.

Replace every example hostname and tenant value in .env before deployment.

## Mutation policy

Mutations are off by default:

~~~bash
BLENDER_MCP_ENABLE_MUTATIONS=false
~~~

For a deliberate editing session, stop the MCP server, change the value to true, and restart it. Return it to false after the session.

The MCP process validates object names, primitive types, transforms, numeric ranges, and positive scales before sending a command to Blender. Every accepted bridge call is written to the configured JSON Lines audit file.

## Tests

The controller and network bridge can be tested without Blender:

~~~bash
.venv/bin/python -m unittest discover -s tests -v
~~~

GitHub Actions also installs the package, compiles the add-on source, and runs the unit tests on pushes and pull requests.

## Security boundaries

- Public MCP access requires a valid signed OAuth token, exact issuer and audience, expiration, subject, and blender.control scope.
- The Blender bridge accepts HTTP only from loopback and requires a separate 32-character-or-longer secret.
- Unknown bridge actions are rejected.
- Request and response sizes are bounded.
- Mutations require an explicit server-side opt-in.
- There is no execute_python, shell, package-manager, credential-reading, or unrestricted filesystem tool.
- Blender API calls execute on Blender's main thread.
- Secrets belong in local protected configuration, never in Git.

## Current MVP limitations

- Blender must remain open with the add-on enabled.
- The first release supports scene inspection, primitive creation, and transforms.
- Saving, deleting, rendering, materials, modifiers, undo checkpoints, and file export are intentionally deferred until their safety policies are defined.
- Integration tests inside a real Blender process are still required before production use.

## License

MIT
