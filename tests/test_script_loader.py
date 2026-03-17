import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import USER_SCRIPTS_ROOT
from app.core.script_loader import ScriptLoader, ScriptValidationError


class ScriptLoaderSecurityTests(unittest.TestCase):
    def test_default_scripts_root_uses_config_strategy(self):
        with patch.dict('os.environ', {}, clear=True):
            loader = ScriptLoader()

        self.assertEqual(loader.user_scripts_root, USER_SCRIPTS_ROOT)

    def _write_script(self, content: str) -> str:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        script_path = Path(temp_dir.name) / "sample.py"
        script_path.write_text(textwrap.dedent(content), encoding="utf-8")
        return str(script_path)

    def test_validate_security_allows_safe_identifiers(self):
        script_path = self._write_script(
            """
            SCRIPT_METADATA = {"name": "safe", "version": "v1.0"}

            def process(frame, config):
                payload = {
                    "exec": "safe field",
                    "open": "still safe",
                    "socket": "text only",
                }
                note = "subprocess.run is just text here"
                return payload, note
            """
        )

        loader = ScriptLoader()
        self.assertTrue(loader.validate_security(script_path))

    def test_validate_security_blocks_exec_alias_from_builtins(self):
        script_path = self._write_script(
            """
            from builtins import exec as builtin_exec

            def process(frame, config):
                builtin_exec("print('x')")
                return {}
            """
        )

        loader = ScriptLoader()
        with self.assertRaises(ScriptValidationError):
            loader.validate_security(script_path)

    def test_validate_security_blocks_os_system_alias(self):
        script_path = self._write_script(
            """
            import os as operating_system

            def process(frame, config):
                operating_system.system("echo unsafe")
                return {}
            """
        )

        loader = ScriptLoader()
        with self.assertRaises(ScriptValidationError):
            loader.validate_security(script_path)


if __name__ == "__main__":
    unittest.main()
