import ast
import json
import os
import re
import unittest
from pathlib import Path
from unittest.mock import patch


class ConfigSecurityTests(unittest.TestCase):
    def test_iclass_api_routes_load_from_json_environment_value(self) -> None:
        config_path = Path(__file__).resolve().parents[1] / "app" / "config.py"
        module = ast.parse(config_path.read_text(encoding="utf-8"))
        settings_module = ast.Module(
            body=[
                node
                for node in module.body
                if isinstance(node, (ast.Import, ast.ImportFrom))
                or isinstance(node, ast.ClassDef) and node.name == "Settings"
            ],
            type_ignores=[],
        )
        namespace = {}
        exec(compile(settings_module, str(config_path), "exec"), namespace)
        settings_class = namespace["Settings"]
        configured_routes = {
            "publish_sign": {
                "path": "/configured/path",
                "fields": {"teacher_code": "configuredTeacherField"},
            }
        }

        with patch.dict(
            os.environ,
            {
                "OPENCLAW_ICLASS_API_ROUTES": json.dumps(configured_routes),
            },
        ):
            loaded = settings_class(_env_file=None)

        self.assertEqual(loaded.iclass_api_routes, configured_routes)

    def test_iclass_api_routes_default_to_empty_mapping(self) -> None:
        config_path = Path(__file__).resolve().parents[1] / "app" / "config.py"
        module = ast.parse(config_path.read_text(encoding="utf-8"))

        settings_class = next(
            node
            for node in module.body
            if isinstance(node, ast.ClassDef) and node.name == "Settings"
        )
        assignment = next(
            node
            for node in settings_class.body
            if isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "iclass_api_routes"
        )

        self.assertIsInstance(assignment.value, ast.Dict)
        self.assertEqual(assignment.value.keys, [])
        self.assertEqual(assignment.value.values, [])

    def test_testing_auth_bypass_is_disabled_by_default(self) -> None:
        config_path = Path(__file__).resolve().parents[1] / "app" / "config.py"
        module = ast.parse(config_path.read_text(encoding="utf-8"))

        settings_class = next(
            node
            for node in module.body
            if isinstance(node, ast.ClassDef) and node.name == "Settings"
        )
        assignment = next(
            node
            for node in settings_class.body
            if isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "session_auth_bypass_for_testing"
        )

        self.assertIsInstance(assignment.value, ast.Constant)
        self.assertIs(assignment.value.value, False)

    def test_source_does_not_embed_local_windows_paths(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        path_pattern = re.compile(r"\b[A-Za-z]:\\[^\r\n\"' ]+\\")
        files_with_local_paths = []

        for source_path in sorted((repository_root / "app").rglob("*.py")):
            source = source_path.read_text(encoding="utf-8")
            if path_pattern.search(source):
                files_with_local_paths.append(
                    source_path.relative_to(repository_root).as_posix()
                )

        self.assertEqual(files_with_local_paths, [])


if __name__ == "__main__":
    unittest.main()
