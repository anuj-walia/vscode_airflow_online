# Architecture (`prod-airflow3`)

## Production topology (GKE)

```mermaid
flowchart TB
    user([Browser]) -->|HTTPS| lb[Google Cloud LB<br/>TLS + managed cert]
    idp[(Enterprise OIDC IdP<br/>MFA + groups)]

    subgraph gke[GKE cluster]
      lb --> proxy[configurable-http-proxy]
      subgraph hubns[Namespace airflow-prod: control plane]
        proxy --> hub[JupyterHub<br/>GenericOAuthenticator + KubeSpawner]
        hubdb[(Hub SQLite PVC)]
        hub --- hubdb
        secret[[OIDC Secret]] -.-> hub
      end
      hub -->|spawns one pod per user| podA
      hub -->|spawns| podB
      subgraph podA[Workspace pod: user A, non-root, no API token]
        lab[JupyterLab :8888<br/>only exposed port]
        subgraph loop[loopback only]
          api[Airflow API server + UI :9091]
          sched[scheduler / dag-processor / triggerer<br/>supervised by workspace_services.py]
          vsc[code-server]
        end
        lab -. jupyter-server-proxy .-> api
        lab -. jupyter-server-proxy .-> vsc
        pvcA[(per-user PVC /opt/airflow<br/>DAGs, logs, SQLite metadata)]
        lab --- pvcA
      end
      podB[Workspace pod: user B]
      np{{NetworkPolicy, ResourceQuota, LimitRange, culling}}
    end

    hub <-->|authorization code flow| idp
    reg[(Artifact Registry<br/>workspace image digest)] --> podA
```

Request path: browser → LB → proxy → Hub (OIDC login) → the user's own
JupyterLab → `jupyter-server-proxy` → Airflow UI or VS Code. Nothing except
JupyterLab listens off-loopback, so another user cannot reach your Airflow.

Airflow 3 task execution: workers call the API server on `127.0.0.1:9091` and keep
the `${JUPYTERHUB_SERVICE_PREFIX}airflow-api/execution/` path prefix.

## Local topologies

```mermaid
flowchart LR
    subgraph A[A. Single container]
      b1([Browser]) -->|127.0.0.1:8888 + token| c1[workspace container<br/>JupyterLab, Airflow 3, VS Code]
      c1 --- v1[(docker volume airflow-ws)]
    end
    subgraph B[B. Docker/OrbStack Kubernetes]
      b2([Browser]) -->|port-forward :8080| h2[Hub] --> w2[workspace pod] --- p2[(local PVC)]
      h2 <--> o2[(OIDC provider)]
    end
```

## DAGs folder selection

`AIRFLOW__CORE__DAGS_FOLDER` is only the default (Dockerfile and chart `extraEnv`).
`workspace_services.py` overrides it per child process from
`$AIRFLOW_HOME/.airflow/dags_folder`, written by the Services page form or
`workspace-dags <path>`. A path must exist and sit inside `$AIRFLOW_HOME`
(excluding `.airflow` and `logs`). Switching stops all three services and starts
dag-processor first. `.airflow/workspace.env`, sourced from `~/.bashrc`, keeps
terminal `airflow` commands in step. The api-server is not restarted: it reads
serialized DAGs from the metadata DB, so the UI follows once the dag-processor has
parsed the new folder.

## Where things live

| Concern | Location |
| :-- | :-- |
| Workspace image | `images/workspace/` (`Dockerfile`, `bin/start.sh`, `bin/workspace_services.py`, `bin/workspace-dags`, `vscode/launch.json`) |
| Hub chart | `charts/airflow-dev-platform/` (z2jh 4.4.2 subchart) |
| Per-environment values | `environments/{dev,prod}.values.yaml` |
| Pinned versions | `VERSIONS.env` |
| Build / deploy | `scripts/build-image.sh`, `scripts/deploy.sh` |

Gaps against the full production plan are listed in `prod_plan.md` (e.g. Hub still on
SQLite, no signing or admission policy).
