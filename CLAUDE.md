# CLAUDE.md

Read `AGENTS.md` first. It is the authoritative cross-branch architecture and verification guide.

## Core context

This repository is a browser-based Airflow development environment. JupyterLab is the gateway; `jupyter-server-proxy` launches and proxies the Airflow UI, `scheduler_wrapper.py`, and VS Code (`code-server`).

Branches are product variants, not a normal linear release sequence:

| Branch | Meaning |
| :-- | :-- |
| `main` | Airflow 2.11.0/Python 3.11 single-container image; source for the `k8s` Airflow 2 image |
| `airflow3` | Airflow 3.1.7/Python 3.11 single-container image; source for the `k8s` Airflow 3 image |
| `k8s` | JupyterHub orchestration that builds and offers those two branch-specific images |
| `unified` | Experimental single image with runtime Airflow/Python selection and per-user PVC caching |

Route changes to the branch that owns the behavior. In particular, editing the root Airflow 2 files on `k8s` does not change the images produced by that branch's `build.sh`; the script checks out `main` and `airflow3` to build them.

## Version-specific behavior

- Airflow 2: `airflow webserver`, `airflow db init`, FAB `airflow users create`, route `/airflow-webserver/`.
- Airflow 3: `airflow api-server`, `airflow db migrate`, SimpleAuthManager password JSON, route `/airflow-api/`, and a separate `airflow dag-processor` in the dedicated branch.
- Dedicated Airflow 3 pins the API server to port 9091. On `k8s`, `AIRFLOW__CORE__EXECUTION_API_SERVER_URL` must call that port directly and retain `${JUPYTERHUB_SERVICE_PREFIX}airflow-api/execution/`.

## Kubernetes paths

The supported default topology is JupyterHub:

```bash
./build.sh
./deploy.sh
# http://localhost:30080
```

`deploy.sh` applies `k8s/namespace.yaml` and `k8s/hub.yaml` only. `launcher/` plus `k8s/launcher.yaml` is an alternative single-shared-pod topology; it is not built or deployed by the default scripts.

`k8s` uses two local image tags and shared `hostPath` DAG/log/plugin directories. `unified` uses `airflow-jupyter:latest`, installs the selected Airflow version at first startup, and stores each user's environment and SQLite DB on a 5 Gi PVC.

## Editing rules

- Start with `git status --short` and `git branch -avv`; preserve unrelated and untracked files.
- Prefer `git show <branch>:<file>` and `git diff <branch>..<branch>` over checking out branches just to inspect them.
- Treat `build.sh` carefully: on `k8s` it stashes the worktree, switches branches, and restores via a trap.
- Put Airflow 2 image changes on `main`, dedicated Airflow 3 changes on `airflow3`, two-image Hub changes on `k8s`, and runtime-selection changes on `unified`.
- Use `AIRFLOW__SECTION__KEY` for actual Airflow settings. `LOAD_EX` and `EXECUTOR` alone are legacy/dead environment values.
- Keep DAG files independent of the scheduler wrapper and use SVG launcher icons.
- Never expose the environment to an untrusted network: Airflow defaults to `admin/admin`, standalone Jupyter and VS Code have no authentication, and JupyterHub permits open signup.

## Known branch debt

- `unified` offers Airflow and Python independently, so incompatible pairs fail during installation instead of being filtered.
- `unified/scheduler_wrapper.py` is the Airflow 2 implementation and lacks the dedicated Airflow 3 DAG-processor launch.
- Several `unified` readmes and the alternate launcher still describe the older two-image architecture; implementation files take precedence.
- At the 2026-09-01 audit, local `airflow3` and `k8s` were each one commit ahead of their remote branches. Recheck before relying on those commits.

## Minimum verification

- DAG: run it with Python, then confirm `airflow dags list` and an `airflow tasks test` in the target version.
- Single container: build, start, and verify JupyterLab plus all three launcher entries at `http://localhost:8888`.
- Kubernetes: build, deploy, spawn the intended profile at `http://localhost:30080`, and inspect Hub/user-pod logs.
- Airflow 3 on `k8s`: execute a task, not merely load the UI; this exercises the direct prefixed execution API route.
- Unified: test first install, cached restart, and version-change reinstall with a supported Airflow/Python pair.
