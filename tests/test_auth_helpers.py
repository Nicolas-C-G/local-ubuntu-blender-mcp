import unittest

from blender_mcp.auth_helpers import audience_contains, normalize_issuer, parse_scopes


class AuthHelperTests(unittest.TestCase):
    def test_normalize_issuer(self) -> None:
        self.assertEqual(
            normalize_issuer("https://tenant.example.com"),
            "https://tenant.example.com/",
        )
        with self.assertRaises(ValueError):
            normalize_issuer("http://tenant.example.com")

    def test_audience_contains_string_or_list(self) -> None:
        self.assertTrue(audience_contains("resource", "resource"))
        self.assertTrue(audience_contains(["other", "resource"], "resource"))
        self.assertFalse(audience_contains(None, "resource"))

    def test_parse_scopes_combines_scope_and_permissions(self) -> None:
        self.assertEqual(
            parse_scopes(
                {
                    "scope": "openid blender.control",
                    "permissions": ["blender.read"],
                }
            ),
            ["blender.control", "blender.read", "openid"],
        )


if __name__ == "__main__":
    unittest.main()
