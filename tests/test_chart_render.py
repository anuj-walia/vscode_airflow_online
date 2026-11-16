"""
Render tests for the Helm chart on both clouds.

The chart ships GKE and AWS ingress plumbing side by side, and the Zero to
JupyterHub subchart merges maps, so a value left in the chart defaults leaks into
every environment. These tests render each environment file with `helm template`
and assert that each cloud gets exactly its own objects, and that a configuration
that would come up unreachable or unprotected is refused at render time.

Skipped when `helm` or the zero-to-jupyterhub subchart (charts/.../charts/) is
missing: run `helm dependency update charts/airflow-dev-platform` first.

Run with:  python3 -m pytest tests/ -q      (or: python3 tests/test_chart_render.py)
"""

from __future__ import annotations

import base64
import json
import pathlib
import re
import shutil
import subprocess
import unittest

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
CHART = REPO_ROOT / "charts/airflow-dev-platform"
HAVE_CHART = shutil.which("helm") is not None and any((CHART / "charts").glob("jupyterhub*"))

DOMAIN = "airflow.example.com"
CERT_ARN = "arn:aws:acm:eu-west-1:111122223333:certificate/abc"
ALB_CERT = r"jupyterhub.ingress.annotations.alb\.ingress\.kubernetes\.io/certificate-arn"
GKE_ONLY_KINDS = {"BackendConfig", "FrontendConfig", "ManagedCertificate"}


def render(
    env: str,
    *extra: str,
    domain: str | None = DOMAIN,
    hosts: str | None = DOMAIN,
    json_sets: tuple[str, ...] = (),
):
    """Return the rendered documents, or raise subprocess.CalledProcessError.

    `json_sets` are `--set-json` items, for values `--set` cannot express (an
    empty map such as a Hub service with no settings).
    """
    cmd = [
        "helm", "template", "t", str(CHART),
        "-f", str(REPO_ROOT / f"environments/{env}.values.yaml"),
        "--set", "jupyterhub.singleuser.image.name=registry.example.com/airflow-workspace",
        "--set-string", "jupyterhub.singleuser.image.tag=3.3.2-abc",
    ]
    if domain is not None:
        cmd += ["--set", f"platform.domain={domain}"]
    if hosts is not None:
        cmd += ["--set", f"jupyterhub.ingress.hosts={{{hosts}}}"]
    for item in extra:
        cmd += ["--set", item]
    for item in json_sets:
        cmd += ["--set-json", item]
    out = subprocess.run(cmd, check=True, capture_output=True, text=True).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def by_kind(docs, kind):
    return [d for d in docs if d["kind"] == kind]


def error_of(*args, **kwargs) -> str:
    try:
        render(*args, **kwargs)
    except subprocess.CalledProcessError as exc:
        return exc.stderr
    raise AssertionError("expected the render to be refused")


@unittest.skipUnless(HAVE_CHART, "needs helm and `helm dependency update` to have run")
class GkeRenderTests(unittest.TestCase):
    def check(self, env):
        docs = render(env)
        kinds = {d["kind"] for d in docs}
        self.assertTrue(GKE_ONLY_KINDS <= kinds, f"{env}: missing {GKE_ONLY_KINDS - kinds}")

        [ingress] = by_kind(docs, "Ingress")
        self.assertEqual(ingress["spec"]["ingressClassName"], "gce")
        annotations = ingress["metadata"]["annotations"]
        self.assertEqual(annotations["networking.gke.io/managed-certificates"], "airflow-dev-platform-cert")
        self.assertEqual(annotations["networking.gke.io/v1beta1.FrontendConfig"], "airflow-dev-platform-frontend")
        self.assertFalse([k for k in annotations if k.startswith("alb.ingress")])

        proxy = next(d for d in by_kind(docs, "Service") if d["metadata"]["name"] == "proxy-public")
        self.assertEqual(proxy["spec"]["type"], "NodePort")
        self.assertIn("cloud.google.com/backend-config", proxy["metadata"]["annotations"])

        self.assertFalse(
            [d for d in by_kind(docs, "StorageClass") if d["provisioner"] == "ebs.csi.aws.com"],
            "AWS StorageClass rendered on GKE",
        )
        return annotations

    def test_prod(self):
        annotations = self.check("prod")
        # Regression: this annotation used to live behind an unused value, so the
        # Ingress never bound to the reserved static IP.
        self.assertEqual(annotations["kubernetes.io/ingress.global-static-ip-name"], "airflow-platform-ip")

    def test_dev(self):
        self.check("dev")

    def test_storage_classes(self):
        docs = render("prod")
        claims = " ".join(yaml.dump(d) for d in docs)
        self.assertIn("premium-rwo", claims)
        self.assertNotIn("gp3", claims)


@unittest.skipUnless(HAVE_CHART, "needs helm and `helm dependency update` to have run")
class AwsRenderTests(unittest.TestCase):
    def setUp(self):
        self.docs = render("aws-prod", f"{ALB_CERT}={CERT_ARN}")

    def test_ingress_is_an_alb(self):
        [ingress] = by_kind(self.docs, "Ingress")
        self.assertEqual(ingress["spec"]["ingressClassName"], "alb")
        annotations = ingress["metadata"]["annotations"]
        self.assertEqual(annotations["alb.ingress.kubernetes.io/certificate-arn"], CERT_ARN)
        self.assertEqual(annotations["alb.ingress.kubernetes.io/target-type"], "ip")
        self.assertEqual(annotations["alb.ingress.kubernetes.io/ssl-redirect"], "443")
        self.assertEqual(annotations["alb.ingress.kubernetes.io/healthcheck-path"], "/hub/health")
        # JupyterLab websockets need the long idle timeout.
        self.assertIn("idle_timeout.timeout_seconds=3600", annotations["alb.ingress.kubernetes.io/load-balancer-attributes"])
        self.assertEqual(ingress["spec"]["rules"][0]["host"], DOMAIN)

    def test_no_gke_objects_or_annotations_leak(self):
        self.assertFalse(GKE_ONLY_KINDS & {d["kind"] for d in self.docs})
        rendered = yaml.dump(self.docs)
        for needle in ("networking.gke.io", "cloud.google.com", "premium-rwo"):
            self.assertNotIn(needle, rendered)

    def test_proxy_service_is_cluster_internal(self):
        proxy = next(d for d in by_kind(self.docs, "Service") if d["metadata"]["name"] == "proxy-public")
        self.assertEqual(proxy["spec"]["type"], "ClusterIP")

    def test_gp3_storage_class(self):
        [storage_class] = by_kind(self.docs, "StorageClass")
        self.assertEqual(storage_class["metadata"]["name"], "gp3")
        self.assertEqual(storage_class["provisioner"], "ebs.csi.aws.com")
        self.assertEqual(storage_class["volumeBindingMode"], "WaitForFirstConsumer")
        self.assertTrue(storage_class["allowVolumeExpansion"])
        self.assertIn("gp3", yaml.dump(self.docs))

    def test_existing_storage_class_can_be_used(self):
        docs = render("aws-prod", f"{ALB_CERT}={CERT_ARN}", "platform.aws.storageClass.create=false")
        self.assertFalse(by_kind(docs, "StorageClass"))


@unittest.skipUnless(HAVE_CHART, "needs helm and `helm dependency update` to have run")
class ValidationTests(unittest.TestCase):
    def test_aws_without_certificate_is_refused(self):
        self.assertIn("ACM certificate", error_of("aws-prod"))

    def test_both_clouds_enabled_is_refused(self):
        err = error_of("aws-prod", f"{ALB_CERT}={CERT_ARN}", "platform.gke.enabled=true")
        self.assertIn("enable exactly one cloud", err)

    def test_missing_domain_is_refused(self):
        self.assertIn("platform.domain must be set", error_of("aws-prod", f"{ALB_CERT}={CERT_ARN}", domain=None))
        self.assertIn("platform.domain must be set", error_of("prod", domain=None))

    def test_domain_not_in_hosts_is_refused(self):
        self.assertIn("not in jupyterhub.ingress.hosts", error_of("prod", hosts="other.example.com"))

    def test_placeholder_image_is_refused(self):
        cmd = [
            "helm", "template", "t", str(CHART), "-f", str(REPO_ROOT / "environments/prod.values.yaml"),
            "--set", f"platform.domain={DOMAIN}", "--set", f"jupyterhub.ingress.hosts={{{DOMAIN}}}",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("placeholder registry", result.stderr)


# The collector reads the Hub's API token from the Secret z2jh makes for every
# entry in hub.services, so the chart refuses to enable it without that entry.
COLLECTOR_SERVICE = ('jupyterhub.hub.services={"otel-collector": {}}',)


def collector_docs(env="waas", *extra):
    return render(env, "observability.enabled=true", *extra, json_sets=COLLECTOR_SERVICE)


def named(docs, kind, name="otel-collector"):
    [doc] = [d for d in docs if d["kind"] == kind and d["metadata"]["name"] == name]
    return doc


@unittest.skipUnless(HAVE_CHART, "needs helm and `helm dependency update` to have run")
class ObservabilityRender(unittest.TestCase):
    def test_observability_off_by_default(self):
        for env, extra in (("dev", ()), ("prod", ()), ("aws-prod", (f"{ALB_CERT}={CERT_ARN}",))):
            with self.subTest(env=env):
                docs = render(env, *extra)
                self.assertFalse([d for d in docs if d["metadata"]["name"].startswith("otel-collector")])

    def test_collector_objects(self):
        docs = collector_docs()
        kinds = {d["kind"] for d in docs if d["metadata"]["name"] == "otel-collector"}
        self.assertEqual(kinds, {"ServiceAccount", "ConfigMap", "Deployment", "NetworkPolicy"})

    def test_collector_scrapes_hub_with_token(self):
        cfg = yaml.safe_load(named(collector_docs(), "ConfigMap")["data"]["config.yaml"])
        job = cfg["receivers"]["prometheus"]["config"]["scrape_configs"][0]
        self.assertEqual(job["metrics_path"], "/hub/metrics")
        self.assertEqual(job["static_configs"][0]["targets"], ["hub:8081"])
        self.assertEqual(job["scrape_interval"], "60s")
        self.assertEqual(job["authorization"]["credentials_file"], "/var/run/hub-token/token")
        pipeline = cfg["service"]["pipelines"]["metrics"]
        self.assertIn("googlemanagedprometheus", pipeline["exporters"])
        self.assertEqual(pipeline["receivers"], ["prometheus"])

    def test_token_comes_from_the_hub_secret(self):
        pod = named(collector_docs(), "Deployment")["spec"]["template"]["spec"]
        [volume] = [v for v in pod["volumes"] if v["name"] == "hub-token"]
        self.assertEqual(volume["secret"]["secretName"], "hub")
        self.assertEqual(
            volume["secret"]["items"],
            [{"key": "hub.services.otel-collector.apiToken", "path": "token"}],
        )

    def test_collector_can_reach_hub(self):
        labels = named(collector_docs(), "Deployment")["spec"]["template"]["metadata"]["labels"]
        self.assertEqual(labels["hub.jupyter.org/network-access-hub"], "true")
        self.assertEqual(labels["app.kubernetes.io/name"], "otel-collector")

    def test_collector_does_not_tolerate_user_nodes(self):
        # Tolerating the workspace pool's taint would let the collector land
        # there and keep the node from scaling back to zero.
        pod = named(collector_docs(), "Deployment")["spec"]["template"]["spec"]
        keys = {t.get("key") for t in pod.get("tolerations", [])}
        self.assertFalse(keys & {"hub.jupyter.org/dedicated", "hub.jupyter.org_dedicated"})

    def test_collector_resources_are_bounded(self):
        container = named(collector_docs(), "Deployment")["spec"]["template"]["spec"]["containers"][0]
        self.assertEqual(container["resources"]["requests"], {"cpu": "50m", "memory": "128Mi"})
        self.assertEqual(container["resources"]["limits"], {"cpu": "200m", "memory": "256Mi"})

    def test_collector_is_locked_down(self):
        pod = named(collector_docs(), "Deployment")["spec"]["template"]["spec"]
        self.assertTrue(pod["securityContext"]["runAsNonRoot"])
        self.assertTrue(pod["containers"][0]["securityContext"]["readOnlyRootFilesystem"])
        policy = named(collector_docs(), "NetworkPolicy")["spec"]
        self.assertEqual(policy["policyTypes"], ["Ingress"])
        self.assertFalse(policy.get("ingress"))

    def test_namespace_reaches_the_exported_metrics(self):
        docs = collector_docs()
        container = named(docs, "Deployment")["spec"]["template"]["spec"]["containers"][0]
        [env] = [e for e in container["env"] if e["name"] == "POD_NAMESPACE"]
        self.assertEqual(env["valueFrom"]["fieldRef"]["fieldPath"], "metadata.namespace")
        cfg = yaml.safe_load(named(docs, "ConfigMap")["data"]["config.yaml"])
        self.assertIn("resource", cfg["service"]["pipelines"]["metrics"]["processors"])

    def test_collector_image_uses_the_pinned_version(self):
        pinned = dict(
            line.split("=", 1) for line in (REPO_ROOT / "VERSIONS.env").read_text().splitlines()
            if line and not line.startswith("#") and "=" in line
        )["OTEL_COLLECTOR_VERSION"]
        container = named(collector_docs(), "Deployment")["spec"]["template"]["spec"]["containers"][0]
        self.assertEqual(container["image"], f"otel/opentelemetry-collector-contrib:{pinned}")

    def test_observability_without_gke_is_refused(self):
        err = error_of(
            "aws-prod", f"{ALB_CERT}={CERT_ARN}", "observability.enabled=true",
            json_sets=COLLECTOR_SERVICE,
        )
        self.assertIn("observability requires platform.gke.enabled", err)

    def test_collector_exports_only_what_the_dashboard_uses(self):
        # The Hub's request-duration histograms alone are hundreds of series and
        # nothing charts them; the filter keeps the Google bill bounded. Every
        # Hub metric the dashboard reads must still pass it.
        cfg = yaml.safe_load(named(collector_docs(), "ConfigMap")["data"]["config.yaml"])
        self.assertIn("filter", cfg["service"]["pipelines"]["metrics"]["processors"])
        [condition] = cfg["processors"]["filter"]["metrics"]["metric"]
        allowed = re.search(r'IsMatch\(name, "(.+)"\)', condition).group(1)
        dashboard = json.loads((REPO_ROOT / "observability/gke-dashboard.json").read_text())
        queries = re.findall(r'"prometheusQuery": "((?:[^"\\]|\\.)*)"', json.dumps(dashboard, indent=1))
        used = {
            name.removesuffix("_bucket")
            for query in queries
            for name in re.findall(r"\b(?:jupyterhub_|waas_)[A-Za-z0-9_]*", query)
        }
        self.assertGreaterEqual(len(used), 5)
        for name in sorted(used):
            with self.subTest(metric=name):
                self.assertRegex(name, allowed)
        self.assertNotRegex("jupyterhub_http_request_duration_seconds", allowed)
        self.assertRegex("up", allowed)

    def test_waas_runs_the_collector_with_a_metrics_only_hub_role(self):
        docs = render("waas")
        named(docs, "Deployment")  # the collector is on in this environment
        secret = named(docs, "Secret", "hub")
        self.assertIn("hub.services.otel-collector.apiToken", secret["data"])
        hub = yaml.safe_load(base64.b64decode(secret["data"]["values.yaml"]))["hub"]
        self.assertEqual(
            hub["loadRoles"]["otel-metrics"],
            {"scopes": ["read:metrics"], "services": ["otel-collector"]},
        )

    def test_observability_without_hub_service_is_refused(self):
        # dev is a GKE environment that defines no Hub service for the collector.
        err = error_of("dev", "observability.enabled=true")
        self.assertIn("jupyterhub.hub.services.otel-collector", err)


if __name__ == "__main__":
    unittest.main()
