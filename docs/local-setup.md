# Local setup (Docker Desktop / OrbStack)

Two ways to run the `prod-airflow3` workspace on a laptop. Pick by what you are
testing.

| Goal | Path | Effort |
| :-- | :-- | :-- |
| Write and debug DAGs; test the workspace image, VS Code debug profiles, Airflow 3 stack | [A. Single container](#a-single-container) | minutes |
| Test the Hub chart, spawner, NetworkPolicy, per-user pods | [B. Docker-based Kubernetes](#b-docker-based-kubernetes-experimental) | an hour, needs an OIDC provider |

Prerequisites: Docker Desktop or OrbStack, Git. For path B also `kubectl` and
`helm` 3.

> Security: the standalone container uses a Jupyter token and an Airflow login
> generated per workspace, but it is a developer tool. Bind it to localhost
> only and never expose it to an untrusted network.

## A. Single container

The workspace image runs standalone when `JUPYTERHUB_API_TOKEN` is absent.

```bash
# 1. Build for your machine's architecture (the script defaults to amd64,
#    which is slow under emulation on Apple silicon).
scripts/build-image.sh --platform linux/arm64 --tag local
#    Intel / Linux: --platform linux/amd64

# 2. Run. The named volume is the workspace: DAGs, logs, SQLite DB, VS Code state.
docker run --rm -it \
  --name airflow-workspace \
  -p 127.0.0.1:8888:8888 \
  -v airflow-ws:/opt/airflow \
  airflow-workspace:local
```

1. Copy the `http://127.0.0.1:8888/lab?token=...` URL from the container log.
2. From the JupyterLab launcher open **VS Code** and **Airflow API** (the Airflow
   UI). The launcher's Services page shows the generated Airflow password.
3. In VS Code open `/opt/airflow`, go to **Run and Debug** and pick
   *Airflow: Test Task* or *Airflow: Test DAG*.

Useful commands:

```bash
docker exec -it airflow-workspace airflow dags list
docker exec -it airflow-workspace cat /opt/airflow/.vscode/launch.json
docker volume rm airflow-ws        # reset to a clean workspace
```

Mount your own DAGs instead of the volume's `dags/` folder:

```bash
docker run --rm -it -p 127.0.0.1:8888:8888 \
  -v airflow-ws:/opt/airflow \
  -v "$PWD/dags":/opt/airflow/dags \
  airflow-workspace:local
```

Settings (environment variables): `WORKSPACE_AIRFLOW_USERNAME`,
`WORKSPACE_AIRFLOW_NO_LOGIN=true` (disable the Airflow login screen; only on
localhost), `WORKSPACE_SEED_EXAMPLES=false`, `WORKSPACE_PORT`.

## B. Docker-based Kubernetes (experimental)

The chart targets GKE. To run it on the Kubernetes built into Docker Desktop or
OrbStack you must turn off the GKE pieces and supply an OIDC provider, because
the Hub deliberately refuses to start without one (no local passwords, no open
signup). This path is **not** exercised by CI; expect to adjust.

1. Enable Kubernetes (Docker Desktop: Settings → Kubernetes; OrbStack: Settings →
   Kubernetes) and confirm `kubectl config current-context`.
2. Build the image. OrbStack and Docker Desktop's Kubernetes use the same image
   store as `docker`, so no push is needed:
   ```bash
   scripts/build-image.sh --platform linux/arm64 --tag local
   ```
3. Create an OIDC client whose redirect URI is
   `http://localhost:8080/hub/oauth_callback` (any OIDC provider; a local
   Keycloak or Dex works). See [oidc-setup.md](oidc-setup.md), then:
   ```bash
   kubectl create namespace airflow-dev
   scripts/create-oidc-secret.sh --namespace airflow-dev \
     --issuer <issuer-url> --client-id <client-id>
   ```
4. Write `environments/local.values.yaml` (not committed):
   ```yaml
   platform:
     gke:
       enabled: false          # no GCE ingress, BackendConfig or ManagedCertificate
   jupyterhub:
     ingress:
       enabled: false
     prePuller:
       hook:       { enabled: false }
       continuous: { enabled: false }
     singleuser:
       image:
         name: airflow-workspace
         tag: local
         pullPolicy: IfNotPresent
       storage:
         dynamic:
           storageClass: local-path   # your cluster's default class; premium-rwo is GKE-only
   ```
   You also need to set `OIDC_ALLOWED_GROUPS`, or `OIDC_ALLOW_ALL_AUTHENTICATED`
   if every account at your local IdP may log in; see `values.yaml`.
5. Install and open the Hub:
   ```bash
   helm dependency update charts/airflow-dev-platform
   helm upgrade --install airflow-platform charts/airflow-dev-platform \
     -n airflow-dev -f environments/dev.values.yaml -f environments/local.values.yaml
   kubectl -n airflow-dev port-forward svc/proxy-public 8080:80
   # http://localhost:8080
   ```
   The OIDC callback URL and `jupyterhub.hub.config` host settings may need to
   match `http://localhost:8080`.
6. Log in, start a server, and open the Airflow API and VS Code launchers.
   Execute a task, not just load the UI: that exercises the prefixed execution
   API route.

Debugging:

```bash
kubectl -n airflow-dev get pods
kubectl -n airflow-dev logs deploy/hub
kubectl -n airflow-dev logs jupyter-<user>
```

Notes: Docker Desktop's default CNI does not enforce NetworkPolicy, so the
isolation rules are present but not tested there. For production, use
[gcp-setup.md](gcp-setup.md).

## Cleanup

```bash
docker rm -f airflow-workspace; docker volume rm airflow-ws
helm -n airflow-dev uninstall airflow-platform; kubectl delete ns airflow-dev
```
