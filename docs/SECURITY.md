# Security Model

## Design goal

Allow an authenticated MCP client to inspect and perform a narrow set of validated Blender operations without exposing arbitrary Python, shell commands, package management, unrestricted file access, or the Blender bridge itself.

This project is not Blender's official Lab MCP server. Blender's official project warns that its generated Python execution is not guarded and recommends an isolated environment. This custom implementation reduces that particular risk by using an operation allowlist, but it does not make AI-controlled 3D editing risk-free.

## Trust boundaries

| Boundary | Protection |
| --- | --- |
| Internet → MCP | HTTPS through Cloudflare; Auth0 JWT validation; audience and scope checks |
| MCP → Blender bridge | Loopback-only HTTP; separate high-entropy bearer token; action allowlist |
| Bridge thread → Blender | Main-thread queue for Blender API calls |
| Read → mutation | Server-side `BLENDER_MCP_ENABLE_MUTATIONS` gate |
| Operations → evidence | JSON Lines audit records |

## Required invariants

- MCP binds only to `127.0.0.1:8001`.
- Blender bridge binds only to `127.0.0.1:8765`.
- Cloudflare routes only the MCP port.
- Auth0 uses a dedicated API Identifier and `blender.control` permission.
- Bridge token has at least 32 high-entropy characters and file mode `600`.
- Mutations default to `false`.
- No arbitrary Python or shell tool is added without a new threat model and explicit project decision.
- Every externally reported success must be based on a successful bridge result.

## Input controls

The current implementation:

- allows only known action names;
- bounds request and response sizes;
- limits object-name length and control characters;
- limits object listings to 200;
- checks finite transform components and numeric bounds;
- requires positive scales;
- allows only enumerated primitive types;
- bounds custom-mesh topology and validates every coordinate and vertex index;
- allows only enumerated modifier types and per-type parameter names, values, and ranges;
- permits deletion of only one exact-name object per call, with no wildcard or bulk mode;
- queues Blender API work onto the main thread.

### Object-deletion policy

`blender_delete_object` is intentionally narrow but destructive. It is protected by the same server-side mutation gate and audit trail as other writes, accepts one validated object name, and is rejected while a turntable capture is active. The bridge deletes the object datablock and unlinks it from all collections and scenes in the open Blender file. It does not delete collections, purge orphaned object data, save the file, or expose a bulk-delete mode. Operators must use a disposable file or checkpoint, verify the exact name, and supervise the call before saving the scene.

### Custom-mesh policy

`blender_create_mesh` accepts data rather than executable code. Both the MCP
controller and Blender bridge independently enforce bounded vertex, edge, face,
and index-reference counts; finite coordinate limits; zero-based index ranges;
and non-degenerate edge and face definitions. The bridge creates the mesh only
through Blender's `from_pydata` API and removes partially created datablocks if
creation fails. The operation remains subject to the mutation gate, audit trail,
request-size limit, duplicate-name check, and turntable mutation lock.

### Modifier policy

`blender_add_modifier` does not expose Blender's unrestricted modifier property
surface. The controller and bridge independently enforce an explicit modifier
type allowlist and a separate parameter allowlist for every supported type.
Values are type-checked and bounded before Blender is changed. Object references
for Boolean and Mirror modifiers are resolved by exact name inside the current
scene; self-references are rejected, and Boolean operands must be meshes. If
Blender rejects a property assignment, the newly created modifier is removed.
The operation adds but never applies a modifier, does not save the file, remains
behind the mutation gate and audit trail, and is blocked during turntable
capture.

## Secret handling

Never commit or share:

- `BLENDER_BRIDGE_TOKEN`;
- OAuth access or refresh tokens;
- Auth0 client secrets;
- Cloudflare tunnel tokens or credential JSON;
- private keys.

Store the bridge token in `$HOME/.config/blender-mcp/env`, mode `600`. Avoid command-line arguments because they may be visible in process listings. Avoid shell history, screenshots, paste sites, logs, and issue descriptions.

Rotate any credential after suspected disclosure. Removing a secret from the latest Git commit is not enough; assume repository history and caches retained it.

## VM isolation

The VM is a risk-reduction boundary, not a guarantee. Use it as a dedicated environment:

- no unrelated personal or production credentials;
- no host home-directory mount;
- minimal shared folders;
- current host, VirtualBox, Ubuntu, Blender, Python, and dependencies;
- snapshots before enabling mutations;
- imported `.blend` files and assets treated as untrusted.

## Residual risks

- A valid authorized client can invoke every enabled tool in its granted scope.
- Allowed operations can still damage a scene or cause resource exhaustion.
- Blender, add-ons, imported files, and Python dependencies may contain vulnerabilities.
- Cloudflare and Auth0 configuration errors may cause denial of service or incorrect authorization.
- VM graphics instability can crash Blender and lose unsaved work.
- Audit writes currently fail open if the audit path cannot be written; operators must monitor log creation.
- Unit tests do not replace integration testing in a real Blender process.

## Adding a new tool

Before adding a tool:

1. Define the narrow user outcome.
2. Reject arbitrary code and unrestricted paths.
3. Validate every input and bound resource usage.
4. Decide whether it is read-only or mutating.
5. Add controller and bridge tests.
6. Add a real-Blender integration test.
7. Add audit coverage and safe error behavior.
8. Update this threat model and acceptance checklist.

Operations such as save, overwrite, render, export, import, open-file, bulk delete, and orphan-data purge require explicit path, overwrite, resource, and confirmation policies before implementation.

## Incident response

If unauthorized activity is suspected:

1. Stop the MCP user service.
2. Close Blender.
3. Remove or disable the Cloudflare hostname route.
4. Revoke Auth0 sessions, client grants, and compromised credentials.
5. Rotate the bridge and tunnel tokens.
6. Preserve audit and service logs.
7. Restore the scene or VM from a known-good checkpoint.
8. Determine the root cause before restoring public access.

## Reference

- [Blender Lab MCP Server](https://www.blender.org/lab/mcp-server/)
