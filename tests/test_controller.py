from __future__ import annotations

import json
import math
import tempfile
import unittest
from pathlib import Path
from typing import Any

from blender_mcp.bridge import BridgeError
from blender_mcp.controller import BlenderController, ControlError


class FakeBridge:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.error: str | None = None

    def call(self, action: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((action, arguments))
        if self.error:
            raise BridgeError(self.error)
        return {"action": action, "arguments": arguments}


class BlenderControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.bridge = FakeBridge()
        self.controller = BlenderController(
            bridge=self.bridge,  # type: ignore[arg-type]
            audit_log=Path(self.temp_dir.name) / "audit.jsonl",
            mutations_enabled=False,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_read_operations_are_available_with_mutations_disabled(self) -> None:
        result = self.controller.list_objects(25)
        self.assertEqual(result["arguments"]["limit"], 25)
        self.assertEqual(self.bridge.calls[0][0], "list_objects")

    def test_list_limit_is_bounded(self) -> None:
        for value in (0, 201, True, 1.5):
            with self.subTest(value=value), self.assertRaises(ControlError):
                self.controller.list_objects(value)  # type: ignore[arg-type]

    def test_create_primitive_requires_mutation_gate(self) -> None:
        with self.assertRaisesRegex(ControlError, "disabled"):
            self.controller.create_primitive("CUBE", "TestCube")
        self.assertEqual(self.bridge.calls, [])

    def test_create_primitive_validates_and_normalizes_arguments(self) -> None:
        self.controller.mutations_enabled = True
        result = self.controller.create_primitive(
            "cube",
            " TestCube ",
            location=(1, 2, 3),
            rotation=(0, 0.5, 1),
            scale=(2, 2, 2),
        )
        arguments = result["arguments"]
        self.assertEqual(arguments["primitive_type"], "CUBE")
        self.assertEqual(arguments["name"], "TestCube")
        self.assertEqual(arguments["location"], [1.0, 2.0, 3.0])

    def test_unsupported_primitive_is_rejected(self) -> None:
        self.controller.mutations_enabled = True
        with self.assertRaisesRegex(ControlError, "Unsupported"):
            self.controller.create_primitive("MONKEY", "Suzanne")

    def test_non_finite_transform_is_rejected(self) -> None:
        self.controller.mutations_enabled = True
        with self.assertRaisesRegex(ControlError, "finite"):
            self.controller.set_transform(
                "Cube",
                [math.nan, 0, 0],
                [0, 0, 0],
                [1, 1, 1],
            )

    def test_non_positive_scale_is_rejected(self) -> None:
        self.controller.mutations_enabled = True
        with self.assertRaisesRegex(ControlError, "greater than zero"):
            self.controller.set_transform(
                "Cube",
                [0, 0, 0],
                [0, 0, 0],
                [1, 0, 1],
            )

    def test_bridge_failure_is_safe_and_audited(self) -> None:
        self.bridge.error = "Blender is unavailable."
        with self.assertRaisesRegex(ControlError, "unavailable"):
            self.controller.health()
        records = [
            json.loads(line)
            for line in self.controller.audit_log.read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(records[0]["tool"], "health")
        self.assertEqual(records[0]["outcome"], "failed")

    def test_success_is_audited(self) -> None:
        self.controller.get_scene()
        record = json.loads(
            self.controller.audit_log.read_text(encoding="utf-8").splitlines()[0]
        )
        self.assertEqual(record["tool"], "get_scene")
        self.assertEqual(record["outcome"], "success")


if __name__ == "__main__":
    unittest.main()
