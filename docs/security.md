# Security posture

What this branch enforces, and — as importantly — what it does not.

## Threat model

Users are employees or trusted contractors authoring and testing DAGs. They
are trusted not to attack the platform, but not trusted to have their
credentials or their code be free of mistakes. The platform's job is to
contain the blast radius of a compromised workspace and to make sure only
authorised people get one.

This is **not** hard multi-tenancy. A workspace runs arbitrary user code in a
container on a shared node; container escape is mitigated, not eliminated. If
your users are mutually untrusted, you need separate node pools or clusters
per tenant, not just separate pods.

## Authentication and authorisation

| Control | Where |
| :-- | :-- |
| Enterprise OIDC is the only login path | `hub.config.JupyterHub.authenticator_class: generic-oauth` |
| No local accounts, no self-registration | `allow_all: false`, `allow_existing_users: false` |
| Authorisation by IdP group | `OIDC_ALLOWED_GROUPS`; no rule means the Hub exits |
| Admin rights by IdP group, scoped | `platform-operator` role — no `admin:users` |
| Re-validation against the IdP every 15 min and before every spawn | `auth_refresh_age`, `refresh_pre_spawn` |
| Sessions are bounded | `cull.timeout` 1h idle, `cull.maxAge` 12h absolute |
| Logout stops the workspace | `shutdown_on_logout: true` |
| PKCE on the authorization code | `enable_pkce: true` |

The fail-closed behaviour is covered by tests — `tests/test_hub_config.py`
executes the shipped config and asserts that a missing issuer, an unreachable
issuer, or a missing authorisation rule all abort startup.

## Workspace isolation

| Control | Where |
| :-- | :-- |
| Non-root, uid/gid 1000 | image `USER`, plus `container_security_context` |
| No privilege escalation, all capabilities dropped, `RuntimeDefault` seccomp | `hub.extraConfig.03-workspace-security` |
| No Kubernetes API token mounted | `automount_service_account_token: false` and `extraPodConfig` |
| No route to the GCE metadata server | `networkPolicy.egressAllowRules.cloudMetadataServer: false` |
| No route to other workspaces or the VPC | `egressAllowRules.privateIPs: false` |
| Per-user volume; no `hostPath` anywhere | `singleuser.storage.type: dynamic` |
| Guaranteed QoS, so one workspace cannot starve another | request == limit |
| Namespace-wide ceiling | `ResourceQuota` + `LimitRange` |

Metadata-server blocking uses NetworkPolicy rather than z2jh's iptables init
container, because that container requires `NET_ADMIN` and privilege
escalation, which the restricted Pod Security Standard forbids. **This means
isolation depends on a NetworkPolicy-enforcing dataplane.** On a cluster
without one, the policies are accepted and ignored. See
[gcp-setup.md](gcp-setup.md#2-gke-cluster).

## Inside the workspace

Everything except JupyterLab binds to loopback:

```
internet -> GCLB (TLS) -> proxy -> jupyterhub-singleuser :8888
                                     -> jupyter-server-proxy
                                          -> airflow api-server  127.0.0.1:9091
                                          -> service supervisor  127.0.0.1:8999
                                          -> code-server         127.0.0.1:<dyn>
```

So `code-server --auth none` is not a hole: reaching it requires a JupyterHub
OAuth session for that specific user. The same applies to the Airflow UI and
the Execution API.

The Airflow UI still gets a credential rather than relying only on that:
a random password generated once per workspace, stored `0600` on the user's
own volume and shown to them on the Services page. There is no `admin/admin`
anywhere on this branch. `WORKSPACE_AIRFLOW_NO_LOGIN=true` removes the login
screen entirely if you prefer to lean wholly on the Hub's session.

## Secrets

The OIDC client secret lives in a Kubernetes Secret created by
`scripts/create-oidc-secret.sh`, which reads it from an environment variable
or a no-echo prompt — never from `argv`, where it would land in shell history
and in `ps`. Nothing in this repository contains a credential, and
`.gitignore` covers the generated password file.

For production, make Secret Manager the source of truth and sync it with
External Secrets Operator or the Secrets Store CSI driver. Enable
[application-layer secret encryption](https://cloud.google.com/kubernetes-engine/docs/how-to/encrypting-secrets)
on the cluster — Kubernetes Secrets are base64, not encrypted, in etcd by
default.

## Known gaps

Deliberate, and worth being explicit about:

1. **Hub state on a zonal PVC.** `sqlite-pvc` is the default. Zone loss is an
   outage with a manual restore. Move to Cloud SQL for an availability
   target — [gcp-setup.md §8](gcp-setup.md#8-optional-hub-state-in-cloud-sql).
2. **No image signing or SBOM.** `build-image.sh` builds, smoke-tests and
   pushes. Adding cosign signing, an SBOM and a vulnerability gate is the
   next step, and belongs in CI rather than a developer's laptop.
3. **No admission policy.** Nothing currently *enforces* Pod Security
   Standards or a registry allowlist at the cluster level. Label the
   namespace `pod-security.kubernetes.io/enforce=restricted` and add
   Gatekeeper or Policy Controller.
4. **Egress to the public internet is open.** Workspaces need PyPI and Git.
   Set `egressAllowRules.nonPrivateIPs: false` and add explicit rules for an
   internal mirror if your policy requires it.
5. **These are development workspaces.** They run `LocalExecutor` against
   per-user SQLite. Nothing here is a production Airflow deployment, and
   nothing here should be scheduling business-critical work — that belongs in
   a separate deployment built on the official Airflow Helm chart, fed from
   Git through CI.
6. **Workspace image vulnerability posture is a point-in-time pin.** Rebuild
   on a schedule, not only when Airflow changes.

## Pre-launch checklist

- [ ] Cluster has Dataplane V2 or Calico, and NetworkPolicy enforcement is verified, not assumed
- [ ] An authenticated user outside `OIDC_ALLOWED_GROUPS` is refused
- [ ] A user removed from the group cannot spawn after `auth_refresh_age`
- [ ] Workspace A cannot reach workspace B, the metadata server, or the Kubernetes API
- [ ] TLS certificate `Active`, HTTP redirects to HTTPS
- [ ] Image deployed by digest or immutable tag; `REPLACE_ME` appears nowhere
- [ ] Hub PVC and user PVCs have a snapshot schedule, and a restore has been performed
- [ ] `tests/test_hub_config.py` passes against the values you are shipping
- [ ] Quota and node-pool maximum agree with each other and with expected concurrency
- [ ] Logs, spawn-failure alerts and an on-call owner exist
