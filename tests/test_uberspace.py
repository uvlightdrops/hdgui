import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch


SPEC = importlib.util.spec_from_file_location("uberspace_setup", "scripts/deploy/uberspace.py")
setup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(setup)


class UberspaceSetupTests(unittest.TestCase):
    def test_private_configuration_is_data_not_executable_shell(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "runtime.json"
            config.write_text(json.dumps({"HDGUI_PUBLIC_BASE_URL": "https://example.org", "HD_API_TOKEN": "$(not-executed)"}))
            config.chmod(0o600)
            with patch.dict(os.environ, {}, clear=True):
                setup.load_environment(config)
                self.assertEqual(os.environ["HD_API_TOKEN"], "$(not-executed)")
                self.assertEqual(os.environ["HDGUI_MULTI_USER"], "1")
                self.assertEqual(os.environ["HDGUI_NO_API"], "1")
            config.chmod(0o644)
            with self.assertRaisesRegex(ValueError, "600"):
                setup.load_environment(config)

    def test_unrelated_environment_keys_and_non_strings_fail(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "runtime.json"
            for data in ({"PATH": "untrusted"}, {"HDGUI_SMTP_PORT": 465}):
                config.write_text(json.dumps(data))
                config.chmod(0o600)
                with self.assertRaises(ValueError):
                    setup.load_environment(config)

    def test_install_generates_both_services_without_secrets_or_overwriting(self):
        for platform, relative in (
            ("u7", "etc/services.d/hdgui.ini"),
            ("u8", ".config/systemd/user/hdgui.service"),
        ):
            with self.subTest(platform=platform), tempfile.TemporaryDirectory() as temp:
                home = Path(temp)
                root = home / "checkout"
                config = home / "runtime.json"
                config.write_text(json.dumps({"HD_API_TOKEN": "private-test-placeholder"}))
                config.chmod(0o600)
                argv = ["uberspace.py", "install", "--platform", platform, "--config", str(config)]
                with patch.object(setup, "ROOT", root), patch.object(Path, "home", return_value=home), \
                     patch("sys.argv", argv), patch.object(setup.subprocess, "run", return_value=Mock(returncode=0)), \
                     patch.dict(os.environ, {}, clear=True), patch("builtins.print"):
                    self.assertEqual(setup.main(), 0)
                    content = (home / relative).read_text()
                    self.assertIn(str(config), content)
                    self.assertIn("8001", content)
                    self.assertNotIn("private-test-placeholder", content)
                    self.assertEqual(setup.main(), 1)
                    self.assertEqual((home / relative).read_text(), content)


if __name__ == "__main__":
    unittest.main()
