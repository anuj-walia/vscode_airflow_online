# AGENTS.md — Repository Guide

> Last audited: 2026-09-20
>
> Repository: `anuj-walia/vscode_airflow_online`
>
> Current branch: `prod-airflow3`
>
> Audit scope: local and available remote-tracking refs for `main`, `airflow3`, `k8s`, `unified`, and `prod-airflow3`

## Project in one sentence

This repository provides browser-based Apache Airflow development environments in which JupyterLab is the gateway and `jupyter-server-proxy` exposes the Airflow UI, a scheduler/log control page, and VS Code (`code-server`).

The most important architectural fact is that branches are product variants. Do not assume a file on one branch is the source used by another branch.

## Branch map

| Branch | Product variant | Airflow/Python strategy | Build output |
| :-- | :-- | :-- | :-- |
| `main` | Single-container Airflow 2 environment | Airflow 2.11.0 on Python 3.11, installed at image build time | `docker compose up --build`; also supplies `airflow-jupyter:airflow2` to `build.sh` on `k8s` |
| `airflow3` | Single-container Airflow 3 environment | Airflow 3.1.7 on Python 3.11, installed with official constraints at image build time | `docker compose up --build`; also supplies `airflow-jupyter:airflow3` to `build.sh` on `k8s` |
| `k8s` | Multi-user JupyterHub orchestration using two prebuilt Airflow images | Profile picker selects the image from `main` or `airflow3` | `airflow-jupyter:airflow2`, `airflow-jupyter:airflow3`, and `airflow-hub:latest` |
| `unified` | Experimental multi-user JupyterHub with one runtime-configurable image | Select Airflow 2.9.3, 2.10.5, 2.11.0, 3.0.1, or 3.1.7 and Python 3.9–3.12; Airflow installs on first pod start | `airflow-jupyter:latest` and `airflow-hub:latest` |
| `prod-airflow3` | **Production Airflow 3 platform on GKE.** OIDC-gated, Helm-deployed, one curated workspace image | Airflow 3.3.2 on Python 3.12, installed with official constraints at image build time | one image pushed to Artifact Registry; JupyterHub comes from the upstream Zero to JupyterHub chart |

`prod-airflow3` is a deliberate break from the other four, not an increment on them. It is the only branch that is production-oriented, the only one with no Airflow 2 path, and the only one that does not build its own Hub image.

### Audited ref status

- `main` matches `origin/main` at `0adeae4`.
- Local `airflow3` is one commit ahead of `origin/airflow3`: `7a9890b` pins the Airflow 3 API server to in-pod port 9091.
- Local `k8s` is one commit ahead of `origin/k8s`: `4bff646` gives Airflow 3 workers a direct, prefix-aware execution API URL.
- `unified` matches `origin/unified` at `6a0821c`.
- `prod-airflow3` branched from `k8s` at `4bff646` and has no remote yet.

These hashes are an audit snapshot, not permanent requirements. Re-run `git fetch --prune` and `git branch -avv` before work that depends on remote state.

## Cross-branch rules

1. A fix to the Airflow 2 image belongs on `main`.
2. A fix to the dedicated Airflow 3 image belongs on `airflow3`.
3. A fix to two-image JupyterHub orchestration belongs on `k8s`.
4. A fix to runtime version selection or the single universal image belongs on `unified`.
5. **Anything production, GKE, OIDC, Helm, or Airflow 3.3 belongs on `prod-airflow3`.**
6. `prod-airflow3` does not inherit fixes from the other branches automatically and mostly should not — its files were rewritten, not copied. The Execution API URL derivation is the one piece of shared logic; see below.
7. Inspect files without switching branches with `git show <branch>:<path>`. This is safer because `build.sh` on `k8s` stashes changes and checks out other branches. `prod-airflow3` has no such script.
8. Never copy Airflow 2 commands into Airflow 3 paths: Airflow 2 uses `airflow webserver`, `airflow db init`, and FAB users; Airflow 3 uses `airflow api-server`, `airflow db migrate`, SimpleAuthManager, and separate `dag-processor` and `triggerer` components.

## Shared request model

```text
Browser
  -> JupyterLab / JupyterHub user server
       -> jupyter-server-proxy
            -> Airflow 2 webserver or Airflow 3 API server
            -> the scheduler control page
            -> code-server
```

On `main`, `airflow3`, `k8s` and `unified` the scheduler does not start at container boot; opening the scheduler launcher presents a form that takes a DAG directory and then starts Airflow subprocesses. **On `prod-airflow3` this is different**: the scheduler, DAG processor and triggerer start automatically under a supervisor, and the launcher entry is a control and log page rather than a setup form.

## The one piece of genuinely shared logic

Airflow 3 under `jupyter-server-proxy` needs two different URLs, and getting it wrong is the failure that recurs across `airflow3`, `k8s` and `prod-airflow3`:

- `AIRFLOW__API__BASE_URL` — what the browser uses, through the authenticated proxy. The API server derives its FastAPI `root_path` from the **path** of this URL, so every route, including the `/execution` mount, lives under `${JUPYTERHUB_SERVICE_PREFIX}airflow-api`.
- `AIRFLOW__CORE__EXECUTION_API_SERVER_URL` — what scheduler and worker processes use. It must **not** traverse the proxy: port 8888 hits the JupyterHub auth layer, every task 403s, and the resulting `HTTPStatusError` is unpicklable, which kills the scheduler. It must hit the API server's fixed in-pod port directly, **with the same path prefix**, or every call 404s.

`k8s` encodes this in `hub/jupyterhub_config.py`'s container command. `prod-airflow3` encodes it in `images/workspace/bin/start.sh`, which logs both URLs at startup. If you change it on one branch, check the other.

## Variant details

### `prod-airflow3`: production Airflow 3 on GKE

The current branch. Full documentation is in `README.md` and `docs/`; this section is the orientation.

- **Airflow**: 3.3.2 on Python 3.12, installed in `/opt/venv` from the official `constraints-3.3.2/constraints-3.12.txt`.
- **Image**: one workspace image built from `images/workspace/`, runs as uid/gid 1000. No Hub image is built — the upstream `quay.io/jupyterhub/k8s-hub` from the chart already ships `oauthenticator`, `kubespawner` and `jupyterhub-idle-culler`.
- **Critical image invariant**: nothing the image needs at runtime may live under `$AIRFLOW_HOME` (`/opt/airflow`). That path is the per-user PVC mount and shadows the image layer. Image content lives in `/srv/workspace` and `/opt/venv`; `bin/start.sh` populates the volume on first boot.
- **Deployment**: `charts/airflow-dev-platform` wraps Zero to JupyterHub 4.4.2 as a dependency. Everything under the `jupyterhub:` key in `values.yaml` is subchart configuration; the chart's own templates add only the GKE ingress plumbing (BackendConfig, FrontendConfig, ManagedCertificate) and namespace guard rails (ResourceQuota, LimitRange).
- **Authentication**: `GenericOAuthenticator` (`authenticator_class: generic-oauth`) against an enterprise OIDC issuer, with endpoints discovered from `/.well-known/openid-configuration` at Hub startup. Authorisation is by IdP group. There is no local account path and no self-registration.
- **Fail-closed by design**: the Hub exits at startup if the issuer is missing, the issuer is unreachable, or no authorisation rule is configured. `tests/test_hub_config.py` executes the shipped config and asserts this.
- **Workspace size**: 1 CPU / 16 GB, request equal to limit (Guaranteed QoS). Defined in exactly four values under `jupyterhub.singleuser.cpu` and `.memory`.
- **Per-user state**: one dynamic PVC mounted at `/opt/airflow`, holding DAGs, logs, plugins, notebooks, editor state and the per-user Airflow SQLite metadata database. There is no shared `hostPath` anywhere and no shared Airflow database.
- **Services**: `bin/workspace_services.py` supervises `airflow scheduler`, `airflow dag-processor` and `airflow triggerer` — restart with backoff, SIGTERM propagation to the process group, size-capped logs, and a `/healthz` endpoint.
- **Credentials**: no `admin/admin`. A random Airflow UI password is generated once per workspace, stored `0600` on the user's volume, and shown on the Services page.

Files that carry a decision rather than mechanics:

| File | What it decides |
| :-- | :-- |
| `VERSIONS.env` | every pin: Airflow, Python, base image, code-server, chart |
| `charts/airflow-dev-platform/values.yaml` | OIDC policy, workspace size, isolation, culling |
| `charts/airflow-dev-platform/templates/_helpers.tpl` | the guards that refuse a bad deployment |
| `images/workspace/bin/start.sh` | volume bootstrap, the two Airflow URLs, credential generation |
| `environments/{dev,prod}.values.yaml` | per-environment overrides; the only place to change size |

### `main`: Airflow 2 single container

- Base `python:3.11-slim`; `apache-airflow==2.11.0` from `requirements.txt`.
- `airflow webserver --port {port}` at proxy route `/airflow-webserver/`.
- `entrypoint.sh` runs `airflow db init` and creates `admin/admin`.
- SQLite plus `SequentialExecutor`. Local entry `http://localhost:8888`.

### `airflow3`: Airflow 3 single container

- Base `python:3.11-slim`; Airflow 3.1.7 with the 3.1.7/Python 3.11 constraints.
- `airflow api-server --port 9091`; the fixed port is required by the `k8s` integration.
- Proxy route `/airflow-api/` with `absolute_url=True`.
- `entrypoint.sh` runs `airflow db migrate` and writes `simple_auth_manager_passwords.json.generated` for `admin/admin`.
- Scheduler wrapper starts both `airflow scheduler` and `airflow dag-processor`.

### `k8s`: two-image JupyterHub

Its root `Dockerfile`, `entrypoint.sh` and `requirements.txt` are an Airflow 2 copy used only by root-level Compose builds; they are not the images `build.sh` produces.

`build.sh` stashes the worktree, checks out `main` and `airflow3` to build `airflow-jupyter:airflow2` and `airflow-jupyter:airflow3`, returns to the starting branch via a trap, and builds `hub/` as `airflow-hub:latest`. Avoid interrupting it during branch transitions.

`hub/jupyterhub_config.py` provides `NativeAuthenticator` self-registration, a two-version profile picker, `imagePullPolicy: Never`, CPU limit 2 / memory 4 GB, a 30-minute idle culler, host-path DAG/log/plugin mounts, and the prefix-aware Airflow 3 URLs described above.

`deploy.sh` applies only `k8s/namespace.yaml` and `k8s/hub.yaml`. Default endpoint `http://localhost:30080`. The Flask launcher under `launcher/` plus `k8s/launcher.yaml` is an alternative single-shared-pod topology that the default scripts neither build nor deploy.

### `unified`: runtime-selected Airflow and Python

- Ubuntu 22.04 base with Python 3.9–3.12 and a separate Jupyter virtual environment. Airflow is deliberately absent from the image.
- `/opt/airflow-scripts/entrypoint.sh` creates `/opt/airflow/venv`, installs the selected release with its constraints, and records `.airflow_version` and `.python_version` on the user's PVC. A version change wipes the cached venv and SQLite database.
- Each user gets a 5 Gi `ReadWriteOnce` PVC at `/opt/airflow`; first startup can take several minutes.

Experimental limitations: the Airflow and Python dropdowns are independent, so unsupported pairs fail during `pip install` rather than being filtered; `scheduler_wrapper.py` is the Airflow 2 version and does not start a DAG processor; several readmes still describe the older two-image design.

## Repository map on `prod-airflow3`

```text
.
├── VERSIONS.env                  # every pinned version, in one place
├── README.md
├── AGENTS.md
├── images/workspace/             # the one workspace image
│   ├── Dockerfile                #   non-root, Airflow 3.3.2 + constraints
│   ├── requirements.txt          #   Jupyter layer only
│   ├── jupyter_server_config.py  #   jupyter-server-proxy registrations
│   ├── bin/start.sh              #   volume bootstrap + Airflow URLs + exec
│   ├── bin/workspace_services.py #   scheduler/dag-processor/triggerer supervisor
│   ├── icons/                    #   SVG launcher icons
│   └── examples/                 #   DAGs seeded into an empty dags folder
├── charts/airflow-dev-platform/
│   ├── Chart.yaml                #   depends on jupyterhub 4.4.2
│   ├── values.yaml               #   OIDC, sizing, isolation, culling
│   └── templates/                #   GKE ingress plumbing + quota/limitrange
├── environments/{dev,prod}.values.yaml
├── scripts/{build-image,create-oidc-secret,deploy}.sh
├── tests/test_hub_config.py
├── docs/{gcp-setup,oidc-setup,operations,security}.md
├── dags/                         # local example DAGs
└── .github/workflows/ci.yml
```

`main` and `airflow3` contain only the single-container subset. `AIRFLOW3_MIGRATION.md` exists only on `airflow3`; `commands.txt` exists only on `unified`.

## Configuration and data

| Concern | Single container | `k8s` | `unified` | `prod-airflow3` |
| :-- | :-- | :-- | :-- | :-- |
| Airflow configuration | `AIRFLOW__SECTION__KEY` in Dockerfile or Compose | Image defaults plus overridden container command | Generated by the runtime entrypoint | Image defaults plus `singleuser.extraEnv`; environment-specific values in the chart |
| DAGs | `./dags` bind mount | `/opt/airflow-shared/dags` host path | Per-user PVC | Per-user PVC at `/opt/airflow/dags` |
| Logs/plugins | Bind mounts | Shared host paths | Per-user PVC | Per-user PVC |
| Airflow DB | Container-local SQLite | Pod-local SQLite | Per-user PVC, reset on version change | Per-user PVC SQLite, migrated on every start |
| Hub DB | N/A | `hub-db-pvc` | `hub-db-pvc` | `hub-db-dir` PVC by default; Cloud SQL documented |
| Authentication | none / `admin/admin` | open self-registration | open self-registration | enterprise OIDC, group-authorised |
| Exposure | localhost:8888 | NodePort 30080 | NodePort 30080 | GCLB with TLS; all in-pod services on loopback |

On `main`, `k8s` and `unified`, `LOAD_EX` and `EXECUTOR` appear in Compose and spawner environments but are not Airflow configuration keys; the effective settings come from `AIRFLOW__CORE__LOAD_EXAMPLES` and `AIRFLOW__CORE__EXECUTOR`. `prod-airflow3` does not use the legacy names at all.

## Common change routing

| Change | Primary location |
| :-- | :-- |
| Airflow 2 package/runtime/proxy | `main` |
| Dedicated Airflow 3 package/runtime/proxy | `airflow3` |
| Two-image profile, pod, execution API, or shared mounts | `k8s`: `hub/jupyterhub_config.py`, `k8s/hub.yaml` |
| Supported runtime version or Python matrix | `unified` |
| Production workspace size, quota, culling | `prod-airflow3`: `charts/airflow-dev-platform/values.yaml`, `environments/*.values.yaml` |
| Production authentication or authorisation | `prod-airflow3`: `jupyterhub.hub.config` and `hub.extraConfig` in `values.yaml`; add a test in `tests/test_hub_config.py` |
| Production Airflow or Python version | `prod-airflow3`: `VERSIONS.env`, then rebuild and redeploy |
| Production workspace startup or Airflow URLs | `prod-airflow3`: `images/workspace/bin/start.sh` |
| GKE ingress, certificate, health check | `prod-airflow3`: `charts/airflow-dev-platform/templates/gke-ingress.yaml` |
| Example DAG | `dags/*.py` and `images/workspace/examples/`; port deliberately across branches |

## Development and verification

Establish branch and worktree state first:

```bash
git status --short
git branch -avv
git log --all --oneline --decorate -n 30
```

Read other variants without checking out:

```bash
git show main:Dockerfile
git show airflow3:entrypoint.sh
git diff --stat k8s..prod-airflow3
```

### `prod-airflow3`

What can be verified without a cluster, and should be before every push:

```bash
python3 tests/test_hub_config.py            # 22 tests: fail-closed auth, sizing, isolation
helm lint charts/airflow-dev-platform \
  --set platform.domain=x.example.com \
  --set 'jupyterhub.ingress.hosts={x.example.com}' \
  --set jupyterhub.singleuser.image.name=example/ws \
  --set jupyterhub.singleuser.image.tag=t
scripts/deploy.sh --env prod --domain x.example.com --image example/ws:t --dry-run
shellcheck -S warning scripts/*.sh images/workspace/bin/*.sh
scripts/build-image.sh                      # builds and smoke-tests the image
```

What needs a cluster:

```bash
scripts/build-image.sh --registry <ar-repo> --push
scripts/create-oidc-secret.sh --namespace airflow-prod --issuer <issuer> --client-id <id>
scripts/deploy.sh --env prod --domain <host> --image <ref>
kubectl -n airflow-prod logs deploy/hub | grep "OIDC configured"
```

Then, in order, and do not skip the second:

1. A member of `OIDC_ALLOWED_GROUPS` logs in and gets a workspace.
2. An authenticated account **outside** that group is refused.
3. The workspace pod is Guaranteed QoS at the configured size: `kubectl get pod <p> -o jsonpath='{.status.qosClass} {.spec.containers[0].resources}'`.
4. A DAG runs a task end to end — this is what exercises the prefixed Execution API route, and merely loading the UI does not.
5. The DAG processor picks up a new file, and a deferrable task resumes (triggerer).
6. The workspace cannot reach `169.254.169.254`, another workspace, or the Kubernetes API.
7. Culling stops an idle workspace and the PVC survives.

### Other branches

```bash
docker compose build && docker compose up      # main, airflow3
./build.sh && ./deploy.sh                      # k8s, unified
kubectl -n airflow-dev get pods
```

Validate DAGs inside an environment with the matching Airflow version:

```bash
python dags/<dag_file>.py
airflow dags list
airflow tasks test <dag_id> <task_id> <logical_date>
```

## Safe editing checklist

- Preserve unrelated worktree changes and untracked DAGs.
- Do not run `build.sh` casually in a dirty tree on `k8s`; it stashes untracked files and switches branches. `prod-airflow3` has no branch-switching script — `scripts/build-image.sh` builds only from `images/workspace/`.
- Do not commit generated Airflow state: `airflow.db`, `airflow.cfg`, logs, plugins, or generated password files.
- Never commit an OIDC client secret, a database password, or a Fernet key. `scripts/create-oidc-secret.sh` reads the secret from the environment or a no-echo prompt, never from `argv`.
- Keep `dags/` independent of the scheduler wrapper or service supervisor.
- `jupyter-server-proxy` `icon_path` values must point to SVG files.
- On `prod-airflow3`, keep image content out of `$AIRFLOW_HOME` — the PVC shadows it.
- On `prod-airflow3`, change a version in `VERSIONS.env`, not in a Dockerfile.
- Rebuild images after dependency, Dockerfile, entrypoint, proxy, extension, or Hub dependency changes.
- If changing URLs under JupyterHub, test with the full `/user/<username>/...` service prefix, not only at `/`.

## Known branch debt

- `unified` offers Airflow and Python independently, so incompatible pairs fail during installation instead of being filtered.
- `unified/scheduler_wrapper.py` is the Airflow 2 implementation and lacks the Airflow 3 DAG-processor launch.
- Several `unified` readmes and the alternate launcher still describe the older two-image architecture; implementation files take precedence.
- `prod-airflow3` keeps Hub state on a zonal PVC by default; Cloud SQL is documented but not wired in. See `docs/security.md`.
- `prod-airflow3` has no image signing, SBOM, or vulnerability gate, and no cluster-level admission policy. Both are named in `docs/security.md#known-gaps`.
- A digest image reference is incompatible with the z2jh image pre-puller. The chart refuses the combination; `docs/operations.md` explains the immutable-tag alternative.
