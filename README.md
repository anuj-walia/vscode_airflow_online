# Airflow 3 Developer Platform

Multi-user Apache Airflow **3.3.2** development workspaces on GKE. Each
authorised user signs in with enterprise SSO and gets their own pod running
JupyterLab, VS Code and a full Airflow 3 stack, with their own persistent
volume.

This is the `prod-airflow3` branch: Airflow 3 only, deployed with Helm,
gated by OIDC. It has no Airflow 2 path, no self-registration, no `admin/admin`
and no local image tags. See [AGENTS.md](AGENTS.md) for how it relates to the
other branches.

```
Browser
  └─ Google Cloud LB — TLS, HTTP→HTTPS, Google-managed certificate
       └─ JupyterHub (Zero to JupyterHub chart)
            ├─ GenericOAuthenticator → your OIDC provider, group-authorised
            └─ KubeSpawner → one workspace pod per user
                 ├─ JupyterLab                          :8888  (the only exposed port)
                 ├─ Airflow API server + UI       127.0.0.1:9091
                 ├─ scheduler · dag-processor · triggerer, supervised
                 ├─ VS Code (code-server)         127.0.0.1:dyn
                 └─ /opt/airflow — per-user PVC: DAGs, logs, SQLite metadata DB
```

Everything but JupyterLab binds to loopback, so reaching the Airflow UI or VS
Code requires a JupyterHub session for that specific user.

See [docs/architecture.md](docs/architecture.md) for diagrams and
[docs/local-setup.md](docs/local-setup.md) to run on Docker Desktop or OrbStack.

## Quick start

```bash
# 1. Cluster, registry, static IP, DNS — docs/gcp-setup.md
# 2. Build and push the workspace image
scripts/build-image.sh --registry europe-west1-docker.pkg.dev/PROJECT/airflow --push

# 3. Store the OIDC client credentials — docs/oidc-setup.md
scripts/create-oidc-secret.sh \
  --namespace airflow-prod \
  --issuer https://login.example.com/oauth2/default \
  --client-id <client-id>

# 4. Deploy
scripts/deploy.sh --env prod \
  --domain airflow.example.com \
  --image europe-west1-docker.pkg.dev/PROJECT/airflow/airflow-workspace:3.3.2-<sha>
```

`deploy.sh` runs a preflight first: cluster reachable, OIDC Secret present,
domain and image supplied, NetworkPolicy enforcement plausible. It refuses to
deploy a configuration that would come up unprotected or unreachable.

## Workspace size

**1 CPU and 16 GB per user pod**, request equal to limit so every workspace is
Guaranteed QoS. Configurable everywhere it matters:

```bash
scripts/deploy.sh ... --cpu 2 --memory 32G
```

or commit it to `environments/<env>.values.yaml`:

```yaml
jupyterhub:
  singleuser:
    cpu:      { limit: 1,   guarantee: 1 }
    memory:   { limit: 16G, guarantee: 16G }
```

Those four values are the only place size is defined — the user-placeholder
pods that keep warm capacity size themselves from them automatically. Add a
`singleuser.profileList` if you want users to pick per spawn;
[docs/operations.md](docs/operations.md#workspace-sizing) has a worked
example, plus quota and node-pool implications.

## Who can log in

Nobody, until you say so. The Hub runs `GenericOAuthenticator` against your
OIDC issuer, discovers its endpoints from
`/.well-known/openid-configuration`, and authorises by IdP group:

```yaml
jupyterhub:
  hub:
    extraEnv:
      OIDC_ALLOWED_GROUPS: "airflow-platform-users"    # may sign in
      OIDC_OPERATOR_GROUPS: "airflow-platform-admins"  # + see/stop others' workspaces
```

If neither that nor an explicit `OIDC_ALLOW_ALL_AUTHENTICATED=true` is set,
**the Hub exits at startup** rather than serving an unprotected login page.
Same if the issuer is missing or unreachable. `tests/test_hub_config.py`
executes the shipped configuration and asserts exactly that.

## Layout

```
VERSIONS.env                      every pin: Airflow, Python, code-server, z2jh
images/workspace/                 the workspace image
  Dockerfile                        non-root, Airflow 3.3.2 + constraints
  bin/start.sh                      volume bootstrap, proxy URLs, db migrate, exec
  bin/workspace_services.py         supervises scheduler/dag-processor/triggerer
  jupyter_server_config.py          jupyter-server-proxy registrations
charts/airflow-dev-platform/      Helm chart wrapping Zero to JupyterHub 4.4.2
  values.yaml                       OIDC, sizing, isolation, culling
  templates/                        GKE BackendConfig/FrontendConfig/cert, quota
environments/{dev,prod}.values.yaml
scripts/{build-image,create-oidc-secret,deploy}.sh
tests/test_hub_config.py
docs/{gcp-setup,oidc-setup,operations,security}.md
```

## Inside a workspace

The JupyterLab launcher has three entries:

- **Airflow UI** — the Airflow 3 API server and web UI. Credentials are on
  the Services page; they are generated per workspace, not shared.
- **Airflow Services** — start, stop and restart the scheduler, DAG processor
  and triggerer, with live log tails and restart counts. All three start
  automatically; this page is for when one of them misbehaves.
- **VS Code** — `code-server`, opened on `/opt/airflow`, with the workspace
  interpreter preselected.

`/opt/airflow` is the user's PVC. DAGs, logs, plugins, notebooks, editor
state and the Airflow SQLite metadata database all live there and survive a
restart, a reschedule and an idle cull.

### Working on multiple projects

The services parse one DAGs folder at a time. It defaults to `/opt/airflow/dags`,
but you can point it at any directory on your volume, for example a project you
just cloned:

```bash
git clone <repo> /opt/airflow/projects/billing
workspace-dags /opt/airflow/projects/billing/dags   # switch + restart services
workspace-dags                                      # print the active folder
```

or type the path into the **DAGs folder** box on the Airflow Services page
(clones under your volume are offered as suggestions). The scheduler,
dag-processor and triggerer restart against the new folder, and the choice is
remembered across restarts. The previous project's DAGs go stale in the UI;
their run history stays in your metadata database. New terminals pick the
folder up from `~/.airflow/workspace.env`. Only the DAGs folder switches, not
`plugins/`.

## Development

```bash
python3 tests/test_hub_config.py            # config and policy tests
helm lint charts/airflow-dev-platform
scripts/deploy.sh --env dev --domain ... --image ... --dry-run

# Run the workspace image standalone (no Hub, token auth on)
scripts/build-image.sh
docker run --rm -p 8888:8888 -v "$PWD/dags:/opt/airflow/dags" airflow-workspace:<tag>
```

## Documentation

- [docs/gcp-setup.md](docs/gcp-setup.md) — cluster, node pools, registry, DNS, Cloud SQL, backups
- [docs/oidc-setup.md](docs/oidc-setup.md) — Okta / Entra / Keycloak registration, claims, offboarding
- [docs/operations.md](docs/operations.md) — sizing, upgrades, runbook, cost
- [docs/security.md](docs/security.md) — posture, known gaps, pre-launch checklist

## Scope

These are **development** workspaces: `LocalExecutor` against per-user SQLite,
sized for authoring and testing DAGs. Production DAGs belong in a separate
Airflow deployment built on the official Airflow Helm chart with external
PostgreSQL, fed from Git through CI. Keeping those two things apart is the
main architectural decision on this branch;
[docs/security.md](docs/security.md#known-gaps) says why.
