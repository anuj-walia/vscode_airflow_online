# Kubernetes Launcher — Airflow Version Selector

Deploy an Airflow dev environment (JupyterLab + VS Code + Airflow) on Kubernetes with a version picker UI.

## Architecture

```
User → http://localhost:30080 (Launcher)
         ↓  select version
     Launcher creates Pod + Service via K8s API
         ↓  pod ready
User → http://localhost:30088 (JupyterLab + Airflow + VS Code)
```

**Three Docker images:**
| Image | Source | Contains |
|-------|--------|----------|
| `airflow-jupyter:airflow2` | `main` branch | Airflow 2.11.0 + JupyterLab + VS Code |
| `airflow-jupyter:airflow3` | `airflow3` branch | Airflow 3.1.7 + JupyterLab + VS Code |
| `airflow-launcher:latest` | `launcher/` | Flask app + Kubernetes Python client |

## Prerequisites

- **Docker Desktop** with Kubernetes enabled (Settings → Kubernetes → Enable)
- **kubectl** installed and configured
- **Git** (the build script checks out branches to build images)

Verify:
```bash
kubectl cluster-info
docker info
```

## Quick Start

```bash
# 1. Build all images (checks out main + airflow3 branches for Airflow builds)
./build.sh

# 2. Deploy launcher to Kubernetes
./deploy.sh

# 3. Open the version selector
open http://localhost:30080
```

## How It Works

1. `build.sh` checks out `main` and `airflow3` branches to build the two Airflow images, then builds the launcher from `launcher/`
2. `deploy.sh` applies K8s manifests: creates namespace `airflow-dev`, RBAC, launcher Deployment + NodePort Service
3. User opens `http://localhost:30080` → sees a styled dropdown to pick Airflow 2 or 3
4. On selection, the launcher uses the Kubernetes API to create a Pod with the corresponding image
5. Once the pod is ready, the user is redirected to `http://localhost:30088` (JupyterLab)

## Useful Commands

```bash
# See all pods
kubectl -n airflow-dev get pods

# Launcher logs
kubectl -n airflow-dev logs -f deploy/launcher

# Manually delete the Airflow workspace pod
kubectl -n airflow-dev delete pod airflow-workspace

# Tear down everything
kubectl delete namespace airflow-dev
```

## File Structure

```
k8s/
├── namespace.yaml           # airflow-dev namespace
├── launcher.yaml            # RBAC + Deployment + Service (NodePort 30080)
└── airflow-pod-template.yaml # Reference template (not applied directly)

launcher/
├── app.py                   # Flask app + Kubernetes client
├── Dockerfile               # Lightweight Python image
├── requirements.txt         # Flask + kubernetes client
└── templates/
    └── index.html           # Styled version picker UI

build.sh                     # Builds all 3 Docker images
deploy.sh                    # Deploys launcher to K8s
```

## Ports

| Port | Service |
|------|---------|
| `30080` | Launcher UI (version selector) |
| `30088` | Airflow workspace (JupyterLab + VS Code + Airflow) |

## Troubleshooting

**Pod stuck in `Pending`:** Check if Docker Desktop Kubernetes is enabled and has enough resources.

**Images not found:** Make sure you ran `./build.sh` first. The images use `imagePullPolicy: Never` (local only).

**Port conflict:** If 30080 or 30088 is in use, edit `k8s/launcher.yaml` (nodePort) and `launcher/app.py` (`AIRFLOW_NODEPORT`).
