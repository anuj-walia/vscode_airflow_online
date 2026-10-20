"""
Tests for the user-selectable DAGs folder in images/workspace/bin/workspace_services.py.

Run with:  python3 -m pytest tests/ -q      (or: python3 tests/test_workspace_services.py)
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
MODULE = REPO_ROOT / "images/workspace/bin/workspace_services.py"


def load(home: str):
    """Import a fresh copy of the module bound to `home` as AIRFLOW_HOME."""
    env = {"AIRFLOW_HOME": home}
    with mock.patch.dict(os.environ, env):
        os.environ.pop("AIRFLOW__CORE__DAGS_FOLDER", None)
        spec = importlib.util.spec_from_file_location("workspace_services_under_test", MODULE)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod  # dataclasses resolve annotations via sys.modules
        spec.loader.exec_module(mod)
    return mod


class DagsFolderTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = os.path.realpath(self._tmp.name)
        self.ws = load(self.home)
        for sub in ("dags", "projects/a/dags", "projects/b/dags", ".airflow", "logs"):
            os.makedirs(os.path.join(self.home, sub), exist_ok=True)

    def test_default_when_nothing_chosen(self):
        self.assertEqual(self.ws.current_dags_folder(), os.path.join(self.home, "dags"))

    def test_env_default_is_honoured(self):
        with mock.patch.dict(os.environ, {"AIRFLOW__CORE__DAGS_FOLDER": "/x/y"}):
            self.assertEqual(self.ws.current_dags_folder(), "/x/y")

    def test_state_file_beats_env(self):
        target = os.path.join(self.home, "projects/a/dags")
        with mock.patch.dict(os.environ, {"AIRFLOW__CORE__DAGS_FOLDER": "/x/y"}):
            _, err = self.ws.set_dags_folder(target, restart=False)
            self.assertIsNone(err)
            self.assertEqual(self.ws.current_dags_folder(), target)

    def test_set_writes_env_file_for_shells(self):
        target = os.path.join(self.home, "projects/a/dags")
        self.ws.set_dags_folder(target, restart=False)
        content = pathlib.Path(self.ws.ENV_FILE).read_text()
        self.assertIn("export AIRFLOW__CORE__DAGS_FOLDER=", content)
        self.assertIn(target, content)

    def test_relative_path_resolves_under_home(self):
        path, err = self.ws.validate_dags_folder("projects/b/dags")
        self.assertIsNone(err)
        self.assertEqual(path, os.path.join(self.home, "projects/b/dags"))

    def test_rejections(self):
        outside = tempfile.mkdtemp()
        self.addCleanup(os.rmdir, outside)
        link = os.path.join(self.home, "escape")
        os.symlink(outside, link)
        for bad in (
            "",
            os.path.join(self.home, "missing"),
            outside,
            os.path.join(self.home, "..", os.path.basename(outside)),
            link,
            os.path.join(self.home, ".airflow"),
            os.path.join(self.home, "logs"),
        ):
            path, err = self.ws.validate_dags_folder(bad)
            self.assertIsNone(path, bad)
            self.assertTrue(err, bad)

    def test_suggestions_find_cloned_projects(self):
        found = self.ws.suggest_dags_folders()
        self.assertIn(os.path.join(self.home, "projects/a/dags"), found)
        self.assertIn(os.path.join(self.home, "dags"), found)

    def test_supervised_child_gets_chosen_folder(self):
        target = os.path.join(self.home, "projects/a/dags")
        self.ws.set_dags_folder(target, restart=False)
        os.makedirs(self.ws.LOG_DIR, exist_ok=True)
        proc = mock.Mock()
        proc.wait.return_value = 0
        service = self.ws.SERVICES["scheduler"]
        with mock.patch.object(self.ws.subprocess, "Popen", return_value=proc) as popen:
            self.ws._supervise(service)
        self.assertEqual(popen.call_args.kwargs["env"]["AIRFLOW__CORE__DAGS_FOLDER"], target)

    def test_stop_requested_prevents_restart(self):
        os.makedirs(self.ws.LOG_DIR, exist_ok=True)
        proc = mock.Mock()
        service = self.ws.SERVICES["scheduler"]

        def die_after_stop():
            service.stop_requested = True
            return -15

        proc.wait.side_effect = die_after_stop
        with mock.patch.object(self.ws.subprocess, "Popen", return_value=proc) as popen:
            self.ws._supervise(service)
        self.assertEqual(popen.call_count, 1)


class HttpTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = os.path.realpath(self._tmp.name)
        os.makedirs(os.path.join(self.home, "projects/a/dags"))
        self.ws = load(self.home)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self.ws.Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}/user/me/airflow-scheduler/"

    def _post(self, path, headers=None):
        data = urllib.parse.urlencode({"dags_folder": path}).encode()
        req = urllib.request.Request(self.base + "dags-folder", data=data, headers=headers or {})
        return urllib.request.urlopen(req)

    def test_json_success_and_healthz(self):
        target = os.path.join(self.home, "projects/a/dags")
        with self._post(target, {"Accept": "application/json"}) as resp:
            self.assertEqual(json.load(resp), {"dags_folder": target})
        with urllib.request.urlopen(self.base + "healthz") as resp:
            self.assertEqual(json.load(resp)["dags_folder"], target)

    def test_json_error(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._post("/etc", {"Accept": "application/json"})
        self.assertEqual(ctx.exception.code, 400)
        self.assertIn("error", json.load(ctx.exception))

    def test_form_error_redirects_with_message_and_page_renders_it(self):
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None

        opener = urllib.request.build_opener(NoRedirect)
        data = urllib.parse.urlencode({"dags_folder": "/etc"}).encode()
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            opener.open(urllib.request.Request(self.base + "dags-folder", data=data))
        self.assertEqual(ctx.exception.code, 303)
        location = ctx.exception.headers["Location"]
        self.assertIn("error=", location)
        with urllib.request.urlopen("http://127.0.0.1:%d%s" % (self.server.server_address[1], location)) as resp:
            page = resp.read().decode()
        self.assertIn("must be inside", page)
        self.assertIn('name="dags_folder"', page)


if __name__ == "__main__":
    unittest.main()
