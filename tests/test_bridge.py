from __future__ import annotations

import base64
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from blender_mcp.bridge import BlenderBridgeClient, BridgeError

TOKEN = "a" * 32


class FakeHandler(BaseHTTPRequestHandler):
    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_POST(self) -> None:
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            self.send_response(401)
            self.end_headers()
            return
        length = int(self.headers["Content-Length"])
        request = json.loads(self.rfile.read(length))
        response = json.dumps({"ok": True, "result": {"received": request["action"]}}).encode(
            "utf-8"
        )
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def do_GET(self) -> None:
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            self.send_response(401)
            self.end_headers()
            return
        payload = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+X2ZkAAAAASUVORK5CYII="
        )
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class BlenderBridgeClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        self.client = BlenderBridgeClient(
            endpoint=f"http://{host}:{port}/command",
            token=TOKEN,
        )

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_authenticated_json_round_trip(self) -> None:
        self.assertEqual(self.client.call("health")["received"], "health")

    def test_collection_action_is_allowlisted(self) -> None:
        result = self.client.call("create_collection", {
            "name": "RobotArm", "object_names": ["Cube"]
        })
        self.assertEqual(result["received"], "create_collection")

    def test_create_mesh_action_is_allowlisted(self) -> None:
        result = self.client.call("create_mesh", {
            "name": "Wing",
            "vertices": [[0, 0, 0], [1, 0, 0], [0, 1, 0]],
            "edges": [],
            "faces": [[0, 1, 2]],
        })
        self.assertEqual(result["received"], "create_mesh")

    def test_add_modifier_action_is_allowlisted(self) -> None:
        result = self.client.call("add_modifier", {
            "object_name": "Wing",
            "modifier_type": "BEVEL",
            "parameters": {"width": 0.1},
        })
        self.assertEqual(result["received"], "add_modifier")

    def test_delete_object_action_is_allowlisted(self) -> None:
        result = self.client.call("delete_object", {"name": "Cube"})
        self.assertEqual(result["received"], "delete_object")

    def test_delete_collection_action_is_allowlisted(self) -> None:
        result = self.client.call("delete_collection", {"name": "RobotArm"})
        self.assertEqual(result["received"], "delete_collection")

    def test_action_allowlist_blocks_unknown_commands(self) -> None:
        with self.assertRaisesRegex(BridgeError, "not allowed"):
            self.client.call("execute_python", {"code": "print('unsafe')"})

    def test_non_loopback_endpoint_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "loopback"):
            BlenderBridgeClient(
                endpoint="https://example.com/command",
                token=TOKEN,
            )

    def test_short_bridge_token_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least 32"):
            BlenderBridgeClient(
                endpoint="http://127.0.0.1:8765/command",
                token="short",
            )

    def test_turntable_frame_is_authenticated_png(self) -> None:
        self.assertTrue(self.client.turntable_frame("a" * 32, 0).startswith(b"\x89PNG"))

    def test_frame_path_validation(self) -> None:
        with self.assertRaisesRegex(BridgeError, "Invalid"):
            self.client.turntable_frame("../etc/passwd", 0)


if __name__ == "__main__":
    unittest.main()
