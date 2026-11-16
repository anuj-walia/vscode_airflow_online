"""
Tests for the Cloud Monitoring dashboard (observability/gke-dashboard.json)
and the script that creates it (scripts/create-dashboard.sh).

The dashboard is data, so a typo in a metric name only shows up as an empty
chart after deployment. These tests pin the metric names, the section layout,
and the script's behaviour, using a stand-in `gcloud` so nothing is created.

Run with:  python3 -m pytest tests/ -q
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import stat
import subprocess
import tempfile
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DASHBOARD = REPO_ROOT / "observability/gke-dashboard.json"
SCRIPT = REPO_ROOT / "scripts/create-dashboard.sh"

# Every metric the dashboard may read. The first group is what GKE exports on
# its own; the rest come from the Hub through the OpenTelemetry Collector.
KNOWN = {
    "kubernetes_io:node_cpu_allocatable_utilization",
    "kube_pod_status_phase",
    "kube_pod_container_status_restarts_total",
    "kubernetes_io:container_memory_used_bytes",
    "jupyterhub_running_servers",
    "jupyterhub_active_users",
    "jupyterhub_server_spawn_duration_seconds_bucket",
    "waas_user_last_activity_timestamp_seconds",
    "waas_user_server_running",
}
METRIC_NAME = re.compile(r"\b(?:kube_|jupyterhub_|waas_|kubernetes_io:)[A-Za-z0-9_:]*")


def load():
    return json.loads(DASHBOARD.read_text())


def prometheus_queries(node):
    """Every PromQL string anywhere in the dashboard."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "prometheusQuery":
                yield value
            else:
                yield from prometheus_queries(value)
    elif isinstance(node, list):
        for item in node:
            yield from prometheus_queries(item)


def sections(dashboard):
    """Group tiles in layout order, each with the tiles inside its area."""
    tiles = dashboard["mosaicLayout"]["tiles"]
    groups = sorted((t for t in tiles if "collapsibleGroup" in t["widget"]), key=lambda t: t["yPos"])
    others = [t for t in tiles if "collapsibleGroup" not in t["widget"]]
    return [
        (g["widget"]["title"], [t for t in others if g["yPos"] <= t["yPos"] < g["yPos"] + g["height"]])
        for g in groups
    ]


class TestDashboardFile(unittest.TestCase):
    def test_every_widget_is_promql(self):
        dashboard = load()
        widgets = [t["widget"] for t in dashboard["mosaicLayout"]["tiles"]
                   if "collapsibleGroup" not in t["widget"]]
        self.assertGreaterEqual(len(widgets), 10)
        for widget in widgets:
            with self.subTest(widget=widget["title"]):
                self.assertTrue(list(prometheus_queries(widget)), "widget has no prometheusQuery")

    def test_metric_names_are_exactly_the_known_set(self):
        used = {name for q in prometheus_queries(load()) for name in METRIC_NAME.findall(q)}
        self.assertEqual(used - KNOWN, set(), "unknown metric name (typo?)")
        self.assertEqual(KNOWN - used, set(), "known metric never charted")

    def test_four_sections_in_order(self):
        self.assertEqual([title for title, _ in sections(load())], ["Cluster", "Pods", "Hub", "Users"])

    def test_every_widget_sits_in_exactly_one_section(self):
        dashboard = load()
        placed = [id(t) for _, members in sections(dashboard) for t in members]
        widgets = [t for t in dashboard["mosaicLayout"]["tiles"] if "collapsibleGroup" not in t["widget"]]
        self.assertEqual(len(placed), len(set(placed)))
        self.assertEqual(len(placed), len(widgets))

    def test_tiles_fit_the_grid_and_do_not_overlap(self):
        tiles = load()["mosaicLayout"]["tiles"]
        columns = load()["mosaicLayout"]["columns"]
        members = [t for t in tiles if "collapsibleGroup" not in t["widget"]]
        for t in tiles:
            self.assertLessEqual(t["xPos"] + t["width"], columns)
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                apart = (
                    a["xPos"] + a["width"] <= b["xPos"] or b["xPos"] + b["width"] <= a["xPos"]
                    or a["yPos"] + a["height"] <= b["yPos"] or b["yPos"] + b["height"] <= a["yPos"]
                )
                self.assertTrue(apart, f"{a['widget']['title']} overlaps {b['widget']['title']}")

    def test_pod_widgets_are_scoped_to_the_platform_namespace(self):
        pods = dict(sections(load()))["Pods"]
        for tile in pods:
            for query in prometheus_queries(tile["widget"]):
                self.assertIn('"airflow-waas"', query)

    def test_gke_metrics_are_scoped_to_this_cluster(self):
        # astronomer-success is a shared project: unfiltered GKE metrics would
        # chart every team's clusters. System metrics label the cluster
        # `cluster_name`; Prometheus-style ones label it `cluster`.
        for query in prometheus_queries(load()):
            if "kubernetes_io:" in query:
                self.assertIn('cluster_name="airflow-waas"', query)
            if "kube_pod_" in query:
                self.assertIn('cluster="airflow-waas"', query)

    def test_users_section_charts_both_per_user_metrics(self):
        users = dict(sections(load()))["Users"]
        queries = " ".join(q for t in users for q in prometheus_queries(t["widget"]))
        self.assertIn("waas_user_last_activity_timestamp_seconds", queries)
        self.assertIn("waas_user_server_running", queries)


class TestCreateScript(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(self.tmp)]))
        # A stand-in gcloud: record the arguments and the config file, create nothing.
        fake = self.tmp / "gcloud"
        fake.write_text(
            "#!/usr/bin/env bash\n"
            f'echo "$@" > "{self.tmp}/args"\n'
            'for a in "$@"; do case "$a" in --config-from-file=*) '
            f'cp "${{a#--config-from-file=}}" "{self.tmp}/config.json";; esac; done\n'
            "echo projects/123/dashboards/abc\n"
        )
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)

    def run_script(self, *args):
        env = {**os.environ, "PATH": f"{self.tmp}:{os.environ['PATH']}"}
        return subprocess.run(
            ["bash", str(SCRIPT), *args], capture_output=True, text=True, env=env
        )

    def test_refuses_without_project(self):
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--project", result.stderr)
        self.assertFalse((self.tmp / "args").exists(), "gcloud must not run")

    def test_rejects_malformed_labels(self):
        result = self.run_script("--project", "p", "--labels", "no-equals-sign")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--labels", result.stderr)
        self.assertFalse((self.tmp / "args").exists(), "gcloud must not run")

    def test_label_keys_must_start_with_a_letter(self):
        for bad in ("1x=y", "-x=y", "_x=y", "ok=1,2bad=y"):
            with self.subTest(labels=bad):
                result = self.run_script("--project", "p", "--labels", bad)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("--labels", result.stderr)
        self.assertFalse((self.tmp / "args").exists(), "gcloud must not run")

    def test_creates_with_labels_and_prints_the_delete_command(self):
        result = self.run_script("--project", "my-proj", "--labels", "lab=gcp-pca,owner=someone")
        self.assertEqual(result.returncode, 0, result.stderr)
        args = (self.tmp / "args").read_text()
        self.assertIn("monitoring dashboards create", args)
        self.assertIn("--project=my-proj", args)
        config = json.loads((self.tmp / "config.json").read_text())
        self.assertEqual(config["labels"], {"lab": "gcp-pca", "owner": "someone"})
        self.assertEqual(config["displayName"], load()["displayName"])
        self.assertIn("projects/123/dashboards/abc", result.stdout)
        self.assertIn("gcloud monitoring dashboards delete projects/123/dashboards/abc", result.stdout)

    def test_works_without_labels(self):
        result = self.run_script("--project", "my-proj")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("labels", json.loads((self.tmp / "config.json").read_text()))


if __name__ == "__main__":
    unittest.main()
