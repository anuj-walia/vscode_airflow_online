"""
Tests for the Hub configuration embedded in charts/.../values.yaml.

The `hub.extraConfig` snippets are executed by JupyterHub at startup with no
validation in front of them, so a mistake there is a CrashLoopBackOff at best
and an unprotected login page at worst. These tests execute the real snippets
against a stub `c` object and assert the security-relevant behaviour:

  * a missing issuer is fatal
  * a missing authorisation rule is fatal -- the platform never comes up
    accepting every authenticated identity by accident
  * group authorisation and the operator role are wired as intended

Run with:  python3 -m pytest tests/ -q      (or: python3 tests/test_hub_config.py)
"""

from __future__ import annotations

import ast
import json
import os
import pathlib
import sys
import unittest
from unittest import mock

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
VALUES = yaml.safe_load((REPO_ROOT / "charts/airflow-dev-platform/values.yaml").read_text())
EXTRA_CONFIG = VALUES["jupyterhub"]["hub"]["extraConfig"]

DISCOVERY = {
    "authorization_endpoint": "https://idp.example.com/authorize",
    "token_endpoint": "https://idp.example.com/token",
    "userinfo_endpoint": "https://idp.example.com/userinfo",
    "end_session_endpoint": "https://idp.example.com/logout",
}


class _Section:
    """Stands in for one traitlets config section (c.GenericOAuthenticator...)."""

    def __init__(self):
        object.__setattr__(self, "_values", {})

    def __setattr__(self, name, value):
        self._values[name] = value

    def __getattr__(self, name):
        # JupyterHub's real Config returns an empty container for an unset
        # trait, which is what `c.JupyterHub.load_roles.append(...)` relies on.
        if name == "load_roles":
            return self._values.setdefault("load_roles", [])
        raise AttributeError(name)

    def get(self, name, default=None):
        return self._values.get(name, default)


class _Config:
    def __init__(self):
        object.__setattr__(self, "_sections", {})

    def __getattr__(self, name):
        return self._sections.setdefault(name, _Section())


def run_snippet(key: str, env: dict[str, str]) -> _Config:
    """Execute one extraConfig snippet with a stub `c` and a controlled env."""
    config = _Config()
    fake_response = mock.MagicMock()
    fake_response.__enter__.return_value = fake_response
    fake_response.read.return_value = json.dumps(DISCOVERY).encode()

    with mock.patch.dict(os.environ, env, clear=True), mock.patch(
        "urllib.request.urlopen", return_value=fake_response
    ):
        exec(  # noqa: S102 - executing the shipped config is the point
            compile(EXTRA_CONFIG[key], f"<{key}>", "exec"),
            {"c": config, "__name__": "hub_config"},
        )
    return config


class TestSnippetsAreValidPython(unittest.TestCase):
    def test_every_snippet_parses(self):
        for key, source in EXTRA_CONFIG.items():
            with self.subTest(snippet=key):
                ast.parse(source)


class TestCallbackUrl(unittest.TestCase):
    def test_callback_is_built_from_the_domain(self):
        config = run_snippet("01-callback", {"PLATFORM_DOMAIN": "airflow.example.com"})
        self.assertEqual(
            config.GenericOAuthenticator.get("oauth_callback_url"),
            "https://airflow.example.com/hub/oauth_callback",
        )

    def test_no_domain_leaves_the_callback_unset(self):
        config = run_snippet("01-callback", {})
        self.assertIsNone(config.GenericOAuthenticator.get("oauth_callback_url"))


class TestOidcFailsClosed(unittest.TestCase):
    def test_missing_issuer_is_fatal(self):
        with self.assertRaises(SystemExit) as raised:
            run_snippet("02-oidc", {})
        self.assertIn("OIDC_ISSUER", str(raised.exception))

    def test_no_authorisation_rule_is_fatal(self):
        """The platform must not start admitting everyone by default."""
        with self.assertRaises(SystemExit) as raised:
            run_snippet("02-oidc", {"OIDC_ISSUER": "https://idp.example.com"})
        self.assertIn("No authorisation rule", str(raised.exception))

    def test_unreachable_issuer_is_fatal(self):
        config_env = {"OIDC_ISSUER": "https://idp.example.com", "OIDC_ALLOWED_GROUPS": "a"}
        with mock.patch.dict(os.environ, config_env, clear=True), mock.patch(
            "urllib.request.urlopen", side_effect=OSError("connection refused")
        ), mock.patch("time.sleep"):
            with self.assertRaises(SystemExit) as raised:
                exec(  # noqa: S102
                    compile(EXTRA_CONFIG["02-oidc"], "<02-oidc>", "exec"),
                    {"c": _Config(), "__name__": "hub_config"},
                )
        self.assertIn("discovery failed", str(raised.exception))


class TestOidcAuthorisation(unittest.TestCase):
    def test_discovery_populates_the_endpoints(self):
        config = run_snippet(
            "02-oidc",
            {"OIDC_ISSUER": "https://idp.example.com/", "OIDC_ALLOWED_GROUPS": "platform-users"},
        )
        authenticator = config.GenericOAuthenticator
        self.assertEqual(authenticator.get("authorize_url"), DISCOVERY["authorization_endpoint"])
        self.assertEqual(authenticator.get("token_url"), DISCOVERY["token_endpoint"])
        self.assertEqual(authenticator.get("userdata_url"), DISCOVERY["userinfo_endpoint"])
        self.assertEqual(authenticator.get("logout_redirect_url"), DISCOVERY["end_session_endpoint"])

    def test_explicit_endpoints_skip_discovery(self):
        env = {
            "OIDC_ISSUER": "https://idp.example.com",
            "OIDC_ALLOWED_GROUPS": "platform-users",
            "OIDC_AUTHORIZE_URL": "https://gw.example.com/authorize",
            "OIDC_TOKEN_URL": "https://gw.example.com/token",
            "OIDC_USERDATA_URL": "https://gw.example.com/userinfo",
        }
        with mock.patch.dict(os.environ, env, clear=True), mock.patch(
            "urllib.request.urlopen", side_effect=AssertionError("discovery should not run")
        ):
            config = _Config()
            exec(  # noqa: S102
                compile(EXTRA_CONFIG["02-oidc"], "<02-oidc>", "exec"),
                {"c": config, "__name__": "hub_config"},
            )
        self.assertEqual(config.GenericOAuthenticator.get("token_url"), env["OIDC_TOKEN_URL"])

    def test_allowed_groups_are_applied_and_allow_all_stays_off(self):
        config = run_snippet(
            "02-oidc",
            {
                "OIDC_ISSUER": "https://idp.example.com",
                "OIDC_ALLOWED_GROUPS": " platform-users , data-eng ",
            },
        )
        self.assertEqual(
            config.GenericOAuthenticator.get("allowed_groups"), {"platform-users", "data-eng"}
        )
        self.assertIsNone(config.Authenticator.get("allow_all"))

    def test_allow_all_requires_an_explicit_opt_in(self):
        config = run_snippet(
            "02-oidc",
            {
                "OIDC_ISSUER": "https://idp.example.com",
                "OIDC_ALLOW_ALL_AUTHENTICATED": "true",
            },
        )
        self.assertTrue(config.Authenticator.get("allow_all"))

    def test_group_allowlist_wins_over_allow_all(self):
        """A stray allow-all flag must not widen an explicit group allowlist."""
        config = run_snippet(
            "02-oidc",
            {
                "OIDC_ISSUER": "https://idp.example.com",
                "OIDC_ALLOWED_GROUPS": "platform-users",
                "OIDC_ALLOW_ALL_AUTHENTICATED": "true",
            },
        )
        self.assertEqual(config.GenericOAuthenticator.get("allowed_groups"), {"platform-users"})
        self.assertIsNone(config.Authenticator.get("allow_all"))

    def test_operator_role_is_scoped_not_admin(self):
        config = run_snippet(
            "02-oidc",
            {
                "OIDC_ISSUER": "https://idp.example.com",
                "OIDC_ALLOWED_GROUPS": "platform-users",
                "OIDC_OPERATOR_GROUPS": "platform-admins",
            },
        )
        roles = config.JupyterHub.load_roles
        self.assertEqual(len(roles), 1)
        role = roles[0]
        self.assertEqual(role["groups"], ["platform-admins"])
        self.assertNotIn("admin:users", role["scopes"])
        self.assertNotIn("admin:servers", role["scopes"])
        self.assertIn("read:servers", role["scopes"])

    def test_no_operator_groups_adds_no_role(self):
        config = run_snippet(
            "02-oidc",
            {"OIDC_ISSUER": "https://idp.example.com", "OIDC_ALLOWED_GROUPS": "platform-users"},
        )
        self.assertEqual(config.JupyterHub.load_roles, [])


class TestWorkspaceSecurityContext(unittest.TestCase):
    def test_restricted_profile(self):
        config = run_snippet("03-workspace-security", {})
        container = config.KubeSpawner.get("container_security_context")
        pod = config.KubeSpawner.get("pod_security_context")
        self.assertTrue(container["runAsNonRoot"])
        self.assertFalse(container["allowPrivilegeEscalation"])
        self.assertEqual(container["capabilities"]["drop"], ["ALL"])
        self.assertEqual(container["seccompProfile"]["type"], "RuntimeDefault")
        self.assertNotEqual(container["runAsUser"], 0)
        self.assertEqual(pod["fsGroup"], 1000)


class TestStaticValues(unittest.TestCase):
    """Guards on the values that carry a security or sizing decision."""

    singleuser = VALUES["jupyterhub"]["singleuser"]

    def test_workspace_default_size_is_one_cpu_sixteen_gb(self):
        self.assertEqual(self.singleuser["cpu"]["limit"], 1)
        self.assertEqual(self.singleuser["cpu"]["guarantee"], 1)
        self.assertEqual(self.singleuser["memory"]["limit"], "16G")
        self.assertEqual(self.singleuser["memory"]["guarantee"], "16G")

    def test_workspace_size_is_guaranteed_qos(self):
        for resource in ("cpu", "memory"):
            self.assertEqual(
                self.singleuser[resource]["limit"],
                self.singleuser[resource]["guarantee"],
                f"{resource} request must equal its limit for Guaranteed QoS",
            )

    def test_workspace_cannot_reach_the_metadata_server(self):
        rules = self.singleuser["networkPolicy"]["egressAllowRules"]
        self.assertFalse(rules["cloudMetadataServer"])
        self.assertFalse(rules["privateIPs"])

    def test_workspace_runs_as_non_root_with_no_api_token(self):
        self.assertEqual(self.singleuser["uid"], 1000)
        self.assertFalse(self.singleuser["allowPrivilegeEscalation"])
        self.assertFalse(self.singleuser["extraPodConfig"]["automountServiceAccountToken"])

    def test_home_is_airflow_home(self):
        self.assertEqual(self.singleuser["storage"]["homeMountPath"], "/opt/airflow")

    def test_authenticator_is_oidc_with_no_implicit_allow(self):
        hub_config = VALUES["jupyterhub"]["hub"]["config"]
        self.assertEqual(hub_config["JupyterHub"]["authenticator_class"], "generic-oauth")
        self.assertFalse(hub_config["Authenticator"]["allow_all"])
        self.assertFalse(hub_config["Authenticator"]["allow_existing_users"])
        self.assertTrue(hub_config["Authenticator"]["enable_auth_state"])
        self.assertTrue(hub_config["JupyterHub"]["shutdown_on_logout"])

    def test_sessions_are_bounded(self):
        cull = VALUES["jupyterhub"]["cull"]
        self.assertTrue(cull["enabled"])
        self.assertGreater(cull["timeout"], 0)
        self.assertGreater(cull["maxAge"], 0, "an unbounded session outlives revoked access")

    def test_proxy_is_not_directly_exposed(self):
        self.assertEqual(VALUES["jupyterhub"]["proxy"]["service"]["type"], "NodePort")


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False, verbosity=2).result.wasSuccessful() else 1)
