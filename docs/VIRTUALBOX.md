# VirtualBox Preparation

This deployment assumes Blender and the MCP server run inside the same Ubuntu Desktop VM.

## Recommended baseline

These are starting recommendations, not hard compatibility guarantees:

| Setting | Recommendation |
| --- | --- |
| Guest OS | Supported 64-bit Ubuntu Desktop release |
| vCPU | 4 when the host has sufficient capacity; minimum 2 for light tests |
| RAM | 8 GB preferred; 4 GB only for small scenes |
| Video memory | Maximum allowed by VirtualBox for the selected controller |
| Graphics controller | VMSVGA |
| 3D acceleration | Enabled, then validated in Blender |
| Network | NAT |
| Port forwarding | None for `8001` or `8765` |
| Disk | 40 GB dynamically allocated or larger |

Do not assign so much CPU or RAM that the host becomes unstable.

## Network model

NAT is preferred because the Cloudflare connector makes an outbound connection. Neither Blender nor the MCP service needs an inbound connection from the LAN or Internet.

Do not switch to Bridged Adapter merely to make the MCP public. Do not expose `8001` or `8765` with VirtualBox port forwarding.

Required outbound access includes:

- Ubuntu package mirrors.
- GitHub.
- Blender downloads.
- Auth0 tenant endpoints.
- Cloudflare tunnel endpoints.

## Guest Additions and graphics

Install the matching Guest Additions using VirtualBox's normal workflow, then reboot. After installing Blender:

```bash
blender --version
```

Open Blender and test viewport navigation, object selection, and creation of a default cube. VirtualBox 3D acceleration is not equivalent to a native GPU; complex scenes, Cycles GPU rendering, and some graphics drivers may be unsuitable in a VM.

If Blender crashes with 3D acceleration enabled, test once with it disabled to separate a graphics-virtualization problem from an MCP problem.

## Snapshots

Create recoverable snapshots at these points:

1. Clean Ubuntu installation and updates.
2. Guest Additions and Blender verified.
3. MCP service, Auth0, and Cloudflare verified with mutations disabled.

Do not include reusable secrets in a snapshot that will be shared or cloned. Rotate the bridge and tunnel tokens after restoring a snapshot to another machine.

## Host security

- Keep VirtualBox and the host OS updated.
- Use full-disk encryption on the host when appropriate.
- Treat `.blend` files and imported assets as untrusted input.
- Keep shared folders read-only or disabled unless a workflow requires them.
- Do not map the host home directory into the VM.
- Use a dedicated VM for experimental MCP control rather than a VM containing unrelated credentials or sensitive data.
