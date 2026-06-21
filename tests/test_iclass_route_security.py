import ast
import importlib.util
import re
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace


EXPECTED_ROUTE_KEYS = {
    "publish_sign",
    "start_lesson",
    "end_lesson",
    "end_sign",
    "submit_sign",
    "manual_sign",
    "get_sign_history",
    "get_sign_result",
    "get_class_overview",
    "list_prepare_courses",
    "list_material_directory",
    "preview_material",
    "download_material",
    "rename_material",
    "move_material",
    "delete_material",
    "create_folder",
    "rename_folder",
    "move_folder",
    "copy_folder",
    "delete_folder",
}

def load_client_module(routes):
    repository_root = Path(__file__).resolve().parents[1]
    client_path = repository_root / "app" / "core" / "iclass" / "client.py"
    config_module = types.ModuleType("app.config")
    config_module.settings = SimpleNamespace(
        iclass_api_base_url="",
        iclass_api_token="",
        iclass_api_routes=routes,
        iclass_api_timeout_seconds=30,
    )
    previous_config = sys.modules.get("app.config")
    sys.modules["app.config"] = config_module
    try:
        spec = importlib.util.spec_from_file_location(
            "iclass_client_route_security_test",
            client_path,
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if previous_config is None:
            sys.modules.pop("app.config", None)
        else:
            sys.modules["app.config"] = previous_config


class IClassRouteSecurityTests(unittest.TestCase):
    def test_client_uses_only_configured_route_keys(self) -> None:
        client_path = (
            Path(__file__).resolve().parents[1]
            / "app"
            / "core"
            / "iclass"
            / "client.py"
        )
        module = ast.parse(client_path.read_text(encoding="utf-8"))
        route_keys = {
            call.args[0].value
            for call in ast.walk(module)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "_route"
            and len(call.args) == 1
            and isinstance(call.args[0], ast.Constant)
            and isinstance(call.args[0].value, str)
        }
        internal_path_literals = {
            node.value
            for node in ast.walk(module)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value.startswith("/")
            and len(node.value) > 1
        }
        internal_request_field_literals = {
            node.value
            for node in ast.walk(module)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and re.fullmatch(r"[a-z]+(?:[A-Z][A-Za-z0-9]*)+", node.value)
        }
        request_dict_keys_are_configured = []
        for function in [
            node
            for node in module.body
            if isinstance(node, ast.ClassDef) and node.name == "IClassApiClient"
            for node in node.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name in EXPECTED_ROUTE_KEYS
        ]:
            for dictionary in [
                node for node in ast.walk(function) if isinstance(node, ast.Dict)
            ]:
                request_dict_keys_are_configured.extend(
                    isinstance(key, ast.Call)
                    and isinstance(key.func, ast.Attribute)
                    and key.func.attr == "_field"
                    for key in dictionary.keys
                )

        self.assertEqual(route_keys, EXPECTED_ROUTE_KEYS)
        self.assertEqual(internal_path_literals, set())
        self.assertEqual(internal_request_field_literals, set())
        self.assertTrue(request_dict_keys_are_configured)
        self.assertTrue(all(request_dict_keys_are_configured))

    def test_route_returns_valid_configured_path(self) -> None:
        module = load_client_module(
            {
                "publish_sign": {
                    "path": "/configured/path",
                    "fields": {"teacher_code": "configuredTeacherField"},
                }
            }
        )
        client = module.IClassApiClient()

        self.assertEqual(client._route("publish_sign"), "/configured/path")
        self.assertEqual(
            client._field("publish_sign", "teacher_code"),
            "configuredTeacherField",
        )

    def test_route_rejects_missing_or_invalid_path(self) -> None:
        invalid_routes = [
            {},
            {"publish_sign": {}},
            {"publish_sign": {"path": "", "fields": {}}},
            {"publish_sign": {"path": "relative/path", "fields": {}}},
            {"publish_sign": {"path": 123, "fields": {}}},
        ]

        for routes in invalid_routes:
            with self.subTest(routes=routes):
                module = load_client_module(routes)
                client = module.IClassApiClient()
                with self.assertRaisesRegex(
                    module.IClassApiError,
                    "Invalid or missing iClass API route: publish_sign",
                ):
                    client._route("publish_sign")

    def test_field_rejects_missing_or_invalid_name(self) -> None:
        invalid_routes = [
            {"publish_sign": {"path": "/configured/path", "fields": {}}},
            {
                "publish_sign": {
                    "path": "/configured/path",
                    "fields": {"teacher_code": ""},
                }
            },
            {
                "publish_sign": {
                    "path": "/configured/path",
                    "fields": {"teacher_code": 123},
                }
            },
        ]

        for routes in invalid_routes:
            with self.subTest(routes=routes):
                module = load_client_module(routes)
                client = module.IClassApiClient()
                with self.assertRaisesRegex(
                    module.IClassApiError,
                    "Invalid or missing iClass API field: "
                    "publish_sign.teacher_code",
                ):
                    client._field("publish_sign", "teacher_code")


if __name__ == "__main__":
    unittest.main()
