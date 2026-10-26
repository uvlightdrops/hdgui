import contextlib
import io
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import click
import httpx
from click.testing import CliRunner

from app import __main__ as entry
from app import backend
from app import token_store
from app.backend import BackendError, ensure_backend, load_config, probe_health

TOKEN = "unit-test-token-value"
HEALTHY = {"status": "ok", "version": "1", "timestamp": "t", "dependencies": {"pyswisseph": "ready"}}


def response(status=200, payload=None):
    return httpx.Response(status, json=HEALTHY if payload is None else payload)


class FakeDocker:
    """Simulates `docker` CLI calls made through subprocess.run."""

    def __init__(self, state=None, fail=None):
        self.state = state
        self.fail = fail or {}
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        action = command[1]
        if action in self.fail:
            return subprocess.CompletedProcess(command, 1, "", self.fail[action])
        if action == "ps":
            return subprocess.CompletedProcess(command, 0, f"{self.state}\n" if self.state else "", "")
        if action in ("run", "start"):
            self.state = "running"
            return subprocess.CompletedProcess(command, 0, "abc123\n", "")
        raise AssertionError(command)

    def actions(self):
        return [command[1] for command, _ in self.calls]


class ConfigTests(unittest.TestCase):
    def test_default_and_local_ports(self):
        config = load_config({})
        self.assertEqual((config.publish, config.container, config.image),
                         ("127.0.0.1:9021:9021", "hd-api", "dturkuler/humandesign_api:latest"))
        config = load_config({"HDGUI_API_URL": "http://localhost:9100/", "HDGUI_API_CONTAINER": "x",
                              "HDGUI_API_IMAGE": "img:1"})
        self.assertEqual((config.publish, config.container, config.image), ("127.0.0.1:9100:9021", "x", "img:1"))
        self.assertEqual(load_config({"HDGUI_API_URL": "http://[::1]:9022"}).publish, "[::1]:9022:9021")

    def test_refuses_non_local_or_non_http(self):
        for url in ("https://127.0.0.1:9021", "http://example.com:9021", "http://10.0.0.2:9021",
                    "http://127.0.0.1:9021/api", "ftp://localhost", "http://u:p@localhost:9021",
                    "http://localhost:99999"):
            with self.subTest(url=url), self.assertRaisesRegex(BackendError, "--no-api"):
                load_config({"HDGUI_API_URL": url})

    def test_health_shape(self):
        with patch.object(backend.httpx, "get", return_value=response()):
            self.assertTrue(probe_health("u")[0])
        bad = [response(503), response(payload={"status": "down"}),
               response(payload={**HEALTHY, "dependencies": {"pyswisseph": "error"}}),
               httpx.Response(200, text="nope")]
        for item in bad:
            with self.subTest(item=item), patch.object(backend.httpx, "get", return_value=item):
                self.assertFalse(probe_health("u")[0])
        with patch.object(backend.httpx, "get", side_effect=httpx.ConnectError("x")):
            self.assertFalse(probe_health("u")[0])


@patch.object(backend.time, "sleep", lambda _: None)
@patch.object(backend.shutil, "which", lambda _: "/usr/bin/docker")
class EnsureBackendTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.token_path = Path(directory.name) / "token"
        token_patch = patch.object(token_store, "TOKEN_FILE", self.token_path)
        token_patch.start()
        self.addCleanup(token_patch.stop)

    def run_ensure(self, docker, health, env=None, timeout=5):
        env = {"HD_API_TOKEN": TOKEN} if env is None else env
        with patch.object(backend.subprocess, "run", docker), \
                patch.object(backend.httpx, "get", side_effect=health), \
                patch.object(backend.click, "echo") as echo:
            try:
                ensure_backend(timeout, env)
            finally:
                self.output = " ".join(str(call.args[0]) for call in echo.call_args_list)

    def test_reuses_healthy_service_without_docker(self):
        docker = FakeDocker()
        self.run_ensure(docker, [response()])
        self.assertEqual(docker.calls, [])
        self.assertIn("weiterverwendet", self.output)

    def test_creates_missing_container_without_token_in_argv(self):
        docker = FakeDocker(state=None)
        self.run_ensure(docker, [httpx.ConnectError("x"), httpx.ConnectError("x"), response()])
        self.assertEqual(docker.actions(), ["ps", "run", "ps"])
        command, kwargs = docker.calls[1]
        self.assertEqual(command, ["docker", "run", "--detach", "--name", "hd-api", "--publish",
                                   "127.0.0.1:9021:9021", "--env", "HD_API_TOKEN",
                                   "dturkuler/humandesign_api:latest"])
        self.assertEqual(kwargs["env"]["HD_API_TOKEN"], TOKEN)
        for call_command, _ in docker.calls:
            self.assertNotIn(TOKEN, " ".join(call_command))
        self.assertNotIn(TOKEN, self.output)

    def test_missing_container_generates_and_persists_token(self):
        docker = FakeDocker(state=None)
        env = {}
        self.run_ensure(docker, [httpx.ConnectError("x"), response()], env=env)
        self.assertEqual(docker.actions(), ["ps", "run"])
        self.assertEqual(self.token_path.read_text().strip(), env["HD_API_TOKEN"])
        self.assertEqual(self.token_path.stat().st_mode & 0o777, 0o600)
        self.assertNotIn(env["HD_API_TOKEN"], self.output)
        self.assertNotIn(env["HD_API_TOKEN"], str(docker.calls[1][0]))

    def test_existing_backend_without_token_fails_without_generation(self):
        for state, health in (("exited", httpx.ConnectError("x")), ("running", response())):
            with self.subTest(state=state), self.assertRaisesRegex(BackendError, "bisherigen Token"):
                self.run_ensure(FakeDocker(state=state), [health], env={})
            self.assertFalse(self.token_path.exists())

    def test_starts_stopped_container_with_saved_token(self):
        token_store.create_saved_token({})
        docker = FakeDocker(state="exited")
        env = {}
        self.run_ensure(docker, [httpx.ConnectError("x"), response()], env=env)
        self.assertEqual(docker.actions(), ["ps", "start"])
        self.assertEqual(env["HD_API_TOKEN"], self.token_path.read_text().strip())

    def test_waits_for_running_unhealthy_container(self):
        docker = FakeDocker(state="running")
        self.run_ensure(docker, [response(503), response(503), response()])
        self.assertEqual(docker.actions(), ["ps", "ps"])

    def test_readiness_timeout(self):
        docker = FakeDocker(state="exited")
        clock = iter(range(0, 1000, 10))
        with patch.object(backend.time, "monotonic", lambda: next(clock)), \
                self.assertRaisesRegex(BackendError, "nicht bereit"):
            self.run_ensure(docker, lambda *a, **k: response(503), timeout=30)

    def test_container_exits_during_wait(self):
        docker = FakeDocker(state="exited")
        original = docker.__call__

        def exits_after_start(command, **kwargs):
            result = original(command, **kwargs)
            if command[1] == "start":
                docker.state = "exited"
            return result
        with self.assertRaisesRegex(BackendError, "docker logs hd-api"):
            self.run_ensure(exits_after_start, httpx.ConnectError("x"))

    def test_unexpected_state_is_not_touched(self):
        docker = FakeDocker(state="paused")
        with self.assertRaisesRegex(BackendError, "paused"):
            self.run_ensure(docker, [httpx.ConnectError("x")])
        self.assertEqual(docker.actions(), ["ps"])

    def test_docker_error_is_reported(self):
        docker = FakeDocker(state=None, fail={"run": "port is already allocated"})
        with self.assertRaisesRegex(BackendError, "docker run.*port is already allocated"):
            self.run_ensure(docker, [httpx.ConnectError("x")])
        docker = FakeDocker(fail={"ps": "Cannot connect to the Docker daemon"})
        with self.assertRaisesRegex(BackendError, "Docker daemon"):
            self.run_ensure(docker, [httpx.ConnectError("x")])

    def test_missing_docker(self):
        with patch.object(backend.shutil, "which", lambda _: None), \
                self.assertRaisesRegex(BackendError, "Docker wurde nicht gefunden"):
            self.run_ensure(FakeDocker(), [httpx.ConnectError("x")])
        with self.assertRaisesRegex(BackendError, "Docker wurde nicht gefunden"):
            self.run_ensure(lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()),
                            [httpx.ConnectError("x")])

    def test_remote_url_is_never_probed_or_launched(self):
        docker = FakeDocker()
        with self.assertRaises(BackendError):
            self.run_ensure(docker, [], env={"HDGUI_API_URL": "http://192.168.1.5:9021"})
        self.assertEqual(docker.calls, [])


class EntryPointTests(unittest.TestCase):
    def setUp(self):
        token_patch = patch.object(entry, "load_saved_token")
        self.load_token = token_patch.start()
        self.addCleanup(token_patch.stop)

    def run_main(self, args):
        with patch.object(entry, "ensure_backend") as ensure, \
                patch.object(entry.uvicorn_cli, "callback") as uvicorn_run, \
                patch.object(entry, "_check_frontend_address") as preflight:
            preflight.side_effect = getattr(self, "preflight_error", None)
            code = entry.main(args)
        return code, ensure, uvicorn_run

    def test_help_and_version_do_not_touch_backend(self):
        for args in (["--help"], ["--version"]):
            with self.subTest(args=args), contextlib.redirect_stdout(io.StringIO()):
                code, ensure, uvicorn_run = self.run_main(args)
            self.assertEqual(code, 0)
            ensure.assert_not_called()
            uvicorn_run.assert_not_called()

    def test_no_api_skips_backend(self):
        code, ensure, uvicorn_run = self.run_main(["--no-api", "--port", "8123"])
        ensure.assert_not_called()
        self.load_token.assert_called_once_with(os.environ)
        self.assertEqual(uvicorn_run.call_args.kwargs["port"], 8123)
        self.assertNotIn("no_api", uvicorn_run.call_args.kwargs)

    def test_backend_started_before_frontend_with_defaults(self):
        code, ensure, uvicorn_run = self.run_main(["--api-timeout", "7"])
        ensure.assert_called_once_with(7.0)
        kwargs = uvicorn_run.call_args.kwargs
        self.assertEqual((kwargs["app"], kwargs["host"], kwargs["port"]), ("app.main:app", "127.0.0.1", 8001))

    def test_backend_failure_prevents_frontend(self):
        with patch.object(entry, "ensure_backend", side_effect=BackendError("kaputt")), \
                patch.object(entry.uvicorn_cli, "callback") as uvicorn_run, \
                patch.object(entry, "_check_frontend_address"), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(entry.main([]), 1)
        uvicorn_run.assert_not_called()

    def test_busy_frontend_prevents_backend_creation(self):
        with socket.socket() as busy:
            busy.bind(("127.0.0.1", 0))
            busy.listen()
            port = busy.getsockname()[1]
            with patch.object(entry, "ensure_backend") as ensure, \
                    patch.object(entry.uvicorn_cli, "callback") as uvicorn_run, contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(entry.main(["--port", str(port)]), 1)
        ensure.assert_not_called()
        uvicorn_run.assert_not_called()

    def test_uvicorn_cli_is_not_mutated(self):
        self.assertNotIn("no_api", [param.name for param in entry.uvicorn_cli.params])


class TokenStoreTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "token"
        token_patch = patch.object(token_store, "TOKEN_FILE", self.path)
        token_patch.start()
        self.addCleanup(token_patch.stop)

    def test_generated_token_is_reused_and_not_printed(self):
        env = {}
        with contextlib.redirect_stderr(io.StringIO()) as output:
            token_store.create_saved_token(env)
        token = env["HD_API_TOKEN"]
        self.assertEqual(len(token), 64)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertNotIn(token, output.getvalue())
        restored = {}
        token_store.load_saved_token(restored)
        self.assertEqual(restored, env)
        token_store.create_saved_token(restored)
        self.assertEqual(restored, env)

    def test_webui_loads_saved_token_even_without_backend_management(self):
        saved = {}
        with contextlib.redirect_stderr(io.StringIO()):
            token_store.create_saved_token(saved)
        with patch.dict(os.environ, {"HD_API_TOKEN": ""}), \
                patch.object(entry, "_check_frontend_address"), \
                patch.object(entry, "ensure_backend") as ensure, \
                patch.object(entry.uvicorn_cli, "callback") as run:
            run.side_effect = lambda **kwargs: self.assertEqual(
                os.environ["HD_API_TOKEN"], saved["HD_API_TOKEN"]
            )
            entry.main(["--no-api"])
            run.assert_called_once()
            ensure.assert_not_called()

    def test_environment_takes_precedence(self):
        self.path.write_text("file-token\n")
        env = {"HD_API_TOKEN": TOKEN}
        token_store.load_saved_token(env)
        self.assertEqual(env["HD_API_TOKEN"], TOKEN)

    def test_missing_file_does_not_generate(self):
        env = {}
        token_store.load_saved_token(env)
        self.assertEqual(env, {})
        self.assertFalse(self.path.exists())

    def test_invalid_file_is_not_overwritten(self):
        for content in ("", "two\nlines\n", "token with spaces", "\x00", "x" * 4097):
            with self.subTest(content=content[:20]):
                self.path.write_text(content)
                self.path.chmod(0o600)
                with self.assertRaisesRegex(click.ClickException, "gültigen"):
                    token_store.load_saved_token({})
                self.assertEqual(self.path.read_text(), content)

    def test_insecure_permissions_and_symlink_are_rejected(self):
        self.path.write_text("token\n")
        self.path.chmod(0o644)
        with self.assertRaisesRegex(click.ClickException, "Rechten 600"):
            token_store.load_saved_token({})
        link = self.path.with_name("link")
        link.symlink_to(self.path)
        with patch.object(token_store, "TOKEN_FILE", link), self.assertRaises(click.ClickException):
            token_store.load_saved_token({})

    def test_creation_error_is_reported(self):
        with patch.object(token_store.os, "open", side_effect=PermissionError()), \
                self.assertRaisesRegex(click.ClickException, "nicht anlegbar"):
            token_store.create_saved_token({})


class BackendEntryPointTests(unittest.TestCase):
    def test_timeout_option_and_environment(self):
        for args, env, expected in (
            ([], {"HDGUI_API_START_TIMEOUT": None}, 60.0),
            ([], {"HDGUI_API_START_TIMEOUT": "90"}, 90.0),
            (["--api-timeout", "7"], {"HDGUI_API_START_TIMEOUT": "90"}, 7.0),
        ):
            with self.subTest(args=args, env=env), patch.object(backend, "ensure_backend") as ensure:
                result = CliRunner().invoke(backend.main, args, env=env)
                self.assertEqual(result.exit_code, 0, result.output)
                ensure.assert_called_once_with(expected)

    def test_invalid_timeout_does_not_start_backend(self):
        for value in ("0", "-1", "invalid"):
            with self.subTest(value=value), patch.object(backend, "ensure_backend") as ensure:
                result = CliRunner().invoke(backend.main, ["--api-timeout", value])
                self.assertEqual(result.exit_code, 2)
                ensure.assert_not_called()

    def test_help_does_not_start_backend(self):
        with patch.object(backend, "ensure_backend") as ensure:
            result = CliRunner().invoke(backend.main, ["--help"])
        self.assertEqual(result.exit_code, 0)
        ensure.assert_not_called()

    def test_failure_exits_nonzero(self):
        with patch.object(backend, "ensure_backend", side_effect=BackendError("Docker kaputt")):
            result = CliRunner().invoke(backend.main)
        self.assertEqual(result.exit_code, 1)
        self.assertIn("Docker kaputt", result.output)


if __name__ == "__main__":
    unittest.main()
