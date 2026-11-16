"""
Tests for the per-user activity metrics registered by the Hub's
`hub.extraConfig.06-activity-metrics` snippet (charts/.../values.yaml).

The snippet runs inside the Hub process and adds a collector to the same
prometheus_client registry that serves /hub/metrics. A bug there would break
the whole metrics endpoint, so these tests execute the real snippet against
stub `jupyterhub` modules and assert:

  * what each user's samples look like (running, stopped, never active)
  * a database failure yields no per-user samples instead of an exception
  * registering the collector does not touch the database (the default
    registry would otherwise call collect() at Hub start, before the DB exists)

Run with:  python3 -m pytest tests/ -q
"""

from __future__ import annotations

import pathlib
import sys
import types
import unittest
from datetime import datetime
from types import SimpleNamespace as ns
from unittest import mock

import yaml

try:
    import prometheus_client
    from prometheus_client import CollectorRegistry
except ImportError:  # pragma: no cover - the Hub image has it, a dev box may not
    prometheus_client = None

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
VALUES = yaml.safe_load((REPO_ROOT / "charts/airflow-dev-platform/values.yaml").read_text())
EXTRA_CONFIG = VALUES["jupyterhub"]["hub"]["extraConfig"]
KEY = "06-activity-metrics"

NOON = datetime(2026, 11, 16, 12, 0, 0)    # naive UTC -> 1794830400.0
EARLY = datetime(2026, 11, 16, 9, 30, 0)   # naive UTC -> 1794821400.0


class _JupyterHub:
    """Stands in for jupyterhub.app.JupyterHub; tests patch `instance`."""

    @classmethod
    def instance(cls):
        raise AssertionError("test must patch JupyterHub.instance")


def load_snippet():
    """Execute the snippet with stub jupyterhub modules; return (namespace, register mock)."""
    app = types.ModuleType("jupyterhub.app")
    app.JupyterHub = _JupyterHub
    orm = types.ModuleType("jupyterhub.orm")
    orm.User = object
    pkg = types.ModuleType("jupyterhub")
    pkg.app, pkg.orm = app, orm
    modules = {"jupyterhub": pkg, "jupyterhub.app": app, "jupyterhub.orm": orm}
    namespace = {"c": mock.MagicMock(), "__name__": "hub_config"}
    with mock.patch.dict(sys.modules, modules), mock.patch.object(
        prometheus_client.REGISTRY, "register"
    ) as register:
        exec(compile(EXTRA_CONFIG[KEY], f"<{KEY}>", "exec"), namespace)  # noqa: S102
    return namespace, register


def samples(families):
    return {(s.name, s.labels["user"]): s.value for f in families for s in f.samples}


def user(name, last_activity, spawners):
    # orm.User.orm_spawners is a dict keyed by server name ("" is the default server).
    return ns(name=name, last_activity=last_activity, orm_spawners=spawners)


@unittest.skipIf(prometheus_client is None, "pip install prometheus_client")
class TestActivityMetrics(unittest.TestCase):
    def setUp(self):
        self.snippet, self.register = load_snippet()

    def families(self, users):
        return self.snippet["activity_metric_families"](users)

    def test_running_user(self):
        u = user("a@x.io", NOON, {"": ns(server=object(), started=EARLY)})
        got = samples(self.families([u]))
        self.assertEqual(got[("waas_user_last_activity_timestamp_seconds", "a@x.io")], 1794830400.0)
        self.assertEqual(got[("waas_user_server_running", "a@x.io")], 1)
        self.assertEqual(got[("waas_user_server_started_timestamp_seconds", "a@x.io")], 1794821400.0)

    def test_stopped_server(self):
        u = user("b@x.io", NOON, {"": ns(server=None, started=None)})
        got = samples(self.families([u]))
        self.assertEqual(got[("waas_user_server_running", "b@x.io")], 0)
        self.assertNotIn(("waas_user_server_started_timestamp_seconds", "b@x.io"), got)

    def test_user_without_activity_has_no_timestamp(self):
        u = user("c@x.io", None, {})
        got = samples(self.families([u]))
        self.assertNotIn(("waas_user_last_activity_timestamp_seconds", "c@x.io"), got)
        self.assertEqual(got[("waas_user_server_running", "c@x.io")], 0)

    def test_started_is_the_earliest_among_running_servers(self):
        u = user("d@x.io", NOON, {
            "": ns(server=object(), started=NOON),
            "second": ns(server=object(), started=EARLY),
            "stopped": ns(server=None, started=NOON),
        })
        got = samples(self.families([u]))
        self.assertEqual(got[("waas_user_server_started_timestamp_seconds", "d@x.io")], 1794821400.0)

    def test_collector_registered_once(self):
        self.assertEqual(self.register.call_count, 1)

    def test_collect_swallows_errors(self):
        with mock.patch.object(_JupyterHub, "instance", side_effect=RuntimeError("db down")):
            with self.assertLogs("JupyterHub", "WARNING") as logged:
                self.assertEqual(list(self.snippet["ActivityCollector"]().collect()), [])
        self.assertIn("db down", logged.output[0])

    def test_collect_reads_users_from_the_hub_database(self):
        users = [user("e@x.io", NOON, {"": ns(server=object(), started=EARLY)})]
        hub = mock.MagicMock()
        hub.db.query.return_value.all.return_value = users
        with mock.patch.object(_JupyterHub, "instance", return_value=hub):
            got = samples(self.snippet["ActivityCollector"]().collect())
        self.assertEqual(got[("waas_user_server_running", "e@x.io")], 1)

    def test_registering_does_not_touch_the_database(self):
        # The default registry runs collect() at registration unless the
        # collector describes itself; at Hub start the database is not open yet.
        with mock.patch.object(_JupyterHub, "instance") as instance:
            CollectorRegistry(auto_describe=True).register(self.snippet["ActivityCollector"]())
        instance.assert_not_called()


if __name__ == "__main__":
    unittest.main()
