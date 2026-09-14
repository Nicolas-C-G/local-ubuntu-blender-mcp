# Deployment Acceptance Checklist

Complete this checklist on the target Ubuntu Desktop VM. Record results without recording secrets.

## Critical gates

- [ ] Ubuntu and VirtualBox versions are recorded.
- [ ] Blender version is recorded and Blender opens reliably in the VM.
- [ ] Repository commit SHA is recorded.
- [ ] Unit tests pass in the deployed virtual environment.
- [ ] Configuration file mode is `600`.
- [ ] No placeholder values remain in the active configuration.
- [ ] MCP listens only on `127.0.0.1:8001`.
- [ ] Blender bridge listens only on `127.0.0.1:8765`.
- [ ] No VirtualBox or router port forwarding exposes either port.
- [ ] No Cloudflare route exposes `8765`.
- [ ] Unauthenticated local `/mcp` request returns `401`.
- [ ] Unauthenticated public `/mcp` request returns `401`.
- [ ] OAuth protected-resource metadata returns `200` JSON.
- [ ] Auth0 issuer, audience, expiration, subject, signature, and `blender.control` scope are enforced.
- [ ] A token for Ubuntu MCP is rejected by Blender MCP.
- [ ] A token without `blender.control` is rejected.
- [ ] A missing or incorrect bridge token returns `401`.
- [ ] Audit file is created and records successful and failed bridge calls.
- [ ] Mutating tools are rejected while mutations are disabled.
- [ ] No arbitrary Python, shell, package, credential, or unrestricted filesystem tool is exposed.

If any critical gate fails, do not enable mutations.

## Functional read-only tests

With Blender open and the custom add-on enabled:

- [ ] `blender_health` reports the real Blender version and current file state.
- [ ] `blender_get_scene` matches the visible scene.
- [ ] `blender_list_objects` returns the visible default objects.
- [ ] `blender_get_object` returns a known object's transform.
- [ ] Unknown object names return a safe error.
- [ ] List limits outside `1..200` are rejected.

## Supervised mutation tests

Perform only after the critical and read-only sections pass. Use a disposable `.blend` file or VM snapshot.

- [ ] Back up or checkpoint the scene.
- [ ] Explicitly enable mutations and restart the MCP service.
- [ ] Create one allowlisted primitive with a unique name.
- [ ] Confirm the object appears in Blender and the returned result matches it.
- [ ] Apply a bounded transform and verify it visually.
- [ ] Unsupported primitive type is rejected.
- [ ] Non-finite or out-of-range transforms are rejected.
- [ ] Non-positive scale is rejected.
- [ ] Duplicate object name is rejected.
- [ ] Each accepted call appears in the audit log.
- [ ] Disable mutations and restart the service immediately after testing.
- [ ] Confirm a mutation is rejected again.

## Recovery tests

- [ ] Closing Blender causes `blender_health` to fail safely.
- [ ] Restarting Blender through the wrapper restores the bridge.
- [ ] Restarting the MCP user service restores the local endpoint.
- [ ] Stopping `cloudflared` removes public reachability without exposing an alternate inbound route.
- [ ] A VM snapshot or `.blend` checkpoint can restore the test scene.

## Acceptance record template

```text
Date:
Operator:
Ubuntu version:
VirtualBox version:
Blender version:
Repository commit:
Public hostname:
Auth0 API Identifier:
Cloudflare tunnel name/ID:
Unit tests: PASS / FAIL
Critical gates: PASS / FAIL
Read-only tests: PASS / FAIL
Mutation tests: PASS / FAIL / NOT RUN
Recovery tests: PASS / FAIL
Known limitations:
Decision: ACCEPTED / REJECTED
```

Do not put secrets or live access tokens in the acceptance record.
