"""Exercise Blender add-on target resolution without starting a GUI."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import types
import unittest
import math
from pathlib import Path
from unittest.mock import patch


class FakeLinks(list[object]):
    def get(self, name: str) -> object | None:
        return next((item for item in self if getattr(item, "name", None) == name), None)

    def __contains__(self, key: object) -> bool:
        if not isinstance(key, str):
            raise TypeError("Blender collection membership expects a name")
        return self.get(key) is not None

    def link(self, item: object) -> None:
        if self.get(getattr(item, "name", "")) is not None:
            raise AssertionError("item is already linked")
        self.append(item)

    def unlink(self, item: object) -> None:
        self.remove(item)


class FakeCollection:
    def __init__(self, name: str, objects: list[object] | None = None) -> None:
        self.name = name
        self.children = FakeLinks()
        self.objects = FakeLinks(objects or [])

    @property
    def all_objects(self) -> list[object]:
        return self.objects + [obj for child in self.children for obj in child.all_objects]


class FakeObject:
    def __init__(self, name: str, object_type: str = "MESH", x: float = 0) -> None:
        self.name = name
        self.type = object_type
        self.matrix_world = FakeMatrix(x)
        self.bound_box = [(a, b, c) for a in (-1, 1) for b in (-1, 1) for c in (-1, 1)]


class FakeObjects(dict[str, FakeObject]):
    def remove(self, obj: FakeObject, *, do_unlink: bool = False) -> None:
        if not do_unlink:
            raise AssertionError("delete_object must unlink the object")
        del self[obj.name]


class FakeCollections(dict[str, FakeCollection]):
    def __iter__(self):
        return iter(self.values())

    def remove(self, collection: FakeCollection, *, do_unlink: bool = False) -> None:
        if not do_unlink:
            raise AssertionError("delete_collection must unlink the collection")
        del self[collection.name]


class FakeVector:
    def __init__(self, values: tuple[float, ...]) -> None:
        self.values = values

    def __getitem__(self, index: int) -> float:
        return self.values[index]

    def __add__(self, other: FakeVector) -> FakeVector:
        return FakeVector(tuple(a + b for a, b in zip(self.values, other.values)))

    def __sub__(self, other: FakeVector) -> FakeVector:
        return FakeVector(tuple(a - b for a, b in zip(self.values, other.values)))

    def __truediv__(self, number: float) -> FakeVector:
        return FakeVector(tuple(value / number for value in self.values))

    @property
    def length(self) -> float:
        return math.sqrt(sum(value * value for value in self.values))


class FakeMatrix:
    def __init__(self, x: float) -> None:
        self.x = x

    def __matmul__(self, point: FakeVector) -> FakeVector:
        return FakeVector((point[0] + self.x, point[1], point[2]))


class AddonPreviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pedestal = FakeObject("Pedestal")
        self.grip = FakeObject("Grip", x=10)
        self.light = FakeObject("Light", "LIGHT")
        self.nested = FakeCollection("Gripper", [self.grip])
        self.assembly = FakeCollection("RobotArm", [self.pedestal, self.light])
        self.assembly.children.append(self.nested)
        self.scene_root = FakeCollection("Scene")
        self.scene_root.children.append(self.assembly)
        self.scene = types.SimpleNamespace(
            collection=self.scene_root,
            objects={obj.name: obj for obj in (self.pedestal, self.grip, self.light)},
        )
        bpy = types.ModuleType("bpy")
        bpy.types = types.SimpleNamespace(Object=FakeObject, Collection=FakeCollection)
        self.data_objects = FakeObjects({
            obj.name: obj for obj in (self.pedestal, self.grip, self.light)
        })
        self.data_collections = FakeCollections({"RobotArm": self.assembly})
        bpy.data = types.SimpleNamespace(
            collections=self.data_collections,
            objects=self.data_objects,
            scenes=[self.scene],
        )
        mathutils = types.ModuleType("mathutils")
        mathutils.Vector = FakeVector
        mathutils.Quaternion = object
        path = Path(__file__).resolve().parents[1] / "blender_addon/blender_mcp_bridge/__init__.py"
        spec = importlib.util.spec_from_file_location("test_blender_bridge_addon", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"bpy": bpy, "mathutils": mathutils,
                                      spec.name: module}):
            spec.loader.exec_module(module)
        self.addon = module
        bpy.context = types.SimpleNamespace(
            scene=self.scene,
            view_layer=types.SimpleNamespace(
                objects=types.SimpleNamespace(active=None), update=lambda: None
            ),
        )

    def test_collection_includes_nested_geometry_but_not_lights(self) -> None:
        objects = self.addon._preview_objects("RobotArm", self.scene)
        self.assertEqual([obj.name for obj in objects], ["Pedestal", "Grip"])

    def test_object_target_still_resolves(self) -> None:
        self.assertEqual(self.addon._preview_objects("Grip", self.scene), [self.grip])

    def test_collection_takes_precedence_when_object_shares_name(self) -> None:
        self.scene.objects["RobotArm"] = FakeObject("RobotArm")
        self.assertEqual(
            [obj.name for obj in self.addon._preview_objects("RobotArm", self.scene)],
            ["Pedestal", "Grip"],
        )

    def test_collection_must_be_in_current_scene_and_have_geometry(self) -> None:
        other = FakeCollection("Other", [FakeObject("MeshOutside")])
        self.addon.bpy.data.collections["Other"] = other
        with self.assertRaisesRegex(ValueError, "current scene"):
            self.addon._preview_objects("Other", self.scene)
        empty = FakeCollection("Empty", [self.light])
        self.scene_root.children.append(empty)
        self.addon.bpy.data.collections["Empty"] = empty
        with self.assertRaisesRegex(ValueError, "no geometry"):
            self.addon._preview_objects("Empty", self.scene)

    def test_collection_mutations_are_blocked_during_preview(self) -> None:
        self.addon._active_preview = "a" * 32
        for action in ("create_collection", "delete_collection"):
            with self.subTest(action=action), self.assertRaisesRegex(ValueError, "unavailable"):
                self.addon._execute({"action": action, "arguments": {}})

    def test_delete_collection_preserves_objects_and_child_collections(self) -> None:
        result = self.addon._execute({
            "action": "delete_collection", "arguments": {"name": "RobotArm"}
        })
        self.assertEqual(result, {
            "deleted": True,
            "name": "RobotArm",
            "preserved_object_count": 2,
            "preserved_child_collection_count": 1,
        })
        self.assertNotIn("RobotArm", self.data_collections)
        self.assertNotIn(self.assembly, list(self.scene_root.children))
        self.assertIn(self.pedestal, list(self.scene_root.objects))
        self.assertIn(self.light, list(self.scene_root.objects))
        self.assertIn(self.nested, list(self.scene_root.children))
        self.assertIn(self.grip, list(self.nested.objects))

    def test_delete_empty_nested_collection_uses_blender_name_lookup(self) -> None:
        empty = FakeCollection("VTOL_Drone")
        self.assembly.children.append(empty)
        self.data_collections["VTOL_Drone"] = empty

        result = self.addon._execute({
            "action": "delete_collection", "arguments": {"name": "VTOL_Drone"}
        })

        self.assertEqual(result, {
            "deleted": True,
            "name": "VTOL_Drone",
            "preserved_object_count": 0,
            "preserved_child_collection_count": 0,
        })
        self.assertNotIn("VTOL_Drone", self.data_collections)
        self.assertNotIn(empty, list(self.assembly.children))

    def test_delete_collection_rejects_collection_outside_current_scene(self) -> None:
        other = FakeCollection("Other")
        self.data_collections["Other"] = other
        with self.assertRaisesRegex(ValueError, "current scene"):
            self.addon._execute({
                "action": "delete_collection", "arguments": {"name": "Other"}
            })

    def test_delete_object_removes_exact_object_and_returns_identity(self) -> None:
        result = self.addon._execute({
            "action": "delete_object", "arguments": {"name": "Grip"}
        })
        self.assertEqual(result, {"deleted": True, "name": "Grip", "type": "MESH"})
        self.assertNotIn("Grip", self.data_objects)
        with self.assertRaisesRegex(ValueError, "does not exist"):
            self.addon._execute({
                "action": "delete_object", "arguments": {"name": "Missing"}
            })

    def test_turntable_frames_combined_collection_bounds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.addon._preview_root = Path(directory)
            with patch.object(self.addon, "_find_viewport", return_value=(None, None, None)):
                result = self.addon._execute({
                    "action": "start_turntable",
                    "arguments": {"name": "RobotArm", "views": 8},
                })
            job = self.addon._previews[result["job_id"]]
            self.assertEqual(job["object_names"], ("Pedestal", "Grip"))
            self.assertAlmostEqual(job["center"][0], 5.0)
            self.assertGreater(job["distance"], 12)


if __name__ == "__main__":
    unittest.main()
