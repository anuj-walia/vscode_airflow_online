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

import pathlib
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


def render(env: str, *extra: str, domain: str | None = DOMAIN, hosts: str | None = DOMAIN):
    """Return the rendered documents, or raise subprocess.CalledProcessError."""
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


if __name__ == "__main__":
    unittest.main()
