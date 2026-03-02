# Kubernetes — Multi-User Airflow Dev Environment

JupyterHub-based launcher that gives each user an isolated pod with JupyterLab + VS Code + Airflow (version selected from a dropdown).

## Architecture

```
User → http://localhost:30080 (JupyterHub)
         ↓  login + select Airflow version
     KubeSpawner creates per-user Pod
         ↓  pod ready
User → JupyterLab + VS Code + Airflow (in their own pod)

     Idle Culler checks every 60s
         ↓  30 min inactivity
     Pod is automatically stopped
```

**Docker images:**
| Image | Source | Purpose |
|-------|--------|---------|
| `airflow-jupyter:airflow2` | `main` branch | Airflow 2.11.0 + JupyterLab + VS Code |
| `airflow-jupyter:airflow3` | `airflow3` branch | Airflow 3.1.7 + JupyterLab + VS Code |
| `airflow-hub:latest` | `hub/` | JupyterHub + KubeSpawner + idle culler |

## Prerequisites

- **Docker Desktop** or **OrbStack** with Kubernetes enabled
- **kubectl** installed  
- **Git**

```bash
kubectl cluster-info
docker info
```

## Quick Start

```bash
# 1. Build all images (~5-10 min first time)
./build.sh

# 2. Deploy JupyterHub to Kubernetes
./deploy.sh

# 3. Open JupyterHub
open http://localhost:30080
```

## User Flow

1. Open `http://localhost:30080` → JupyterHub login
2. First time → click **Sign Up**, create username + password
3. Log in → profile picker: **Airflow 2.11.0** or **Airflow 3.1.7**
4. Click **Start** → pod spins up (~30-60s)
5. Redirected to your JupyterLab with VS Code + Airflow
6. After **30 minutes idle**, pod is automatically stopped
7. Return to JupyterHub to restart anytime

## Configuration

### Idle Timeout
Edit `hub/jupyterhub_config.py`:
```python
"--timeout=1800",   # 30 min (change to e.g. 3600 for 1 hour)
```

### Resource Limits
```python
c.KubeSpawner.cpu_limit = 2        # Max 2 CPUs per user
c.KubeSpawner.mem_limit = "4G"     # Max 4GB RAM per user
```

### Authentication
Currently uses `NativeAuthenticator` (self-registration). To switch to GitHub OAuth:
```python
c.JupyterHub.authenticator_class = "oauthenticator.GitHubOAuthenticator"
c.GitHubOAuthenticator.client_id = "..."
c.GitHubOAuthenticator.client_secret = "..."
```

## Useful Commands

```bash
# See all pods (hub + user pods)
kubectl -n airflow-dev get pods

# JupyterHub logs
kubectl -n airflow-dev logs -f deploy/hub

# List user servers
kubectl -n airflow-dev get pods -l component=singleuser-server

# Tear down everything
kubectl delete namespace airflow-dev
```

## File Structure

```
hub/
├── jupyterhub_config.py     # KubeSpawner + profiles + idle culler
├── Dockerfile               # JupyterHub image
└── requirements.txt         # jupyterhub, kubespawner, etc.

k8s/
├── namespace.yaml           # airflow-dev namespace
├── hub.yaml                 # RBAC + PVC + Deployment + Service
└── airflow-pod-template.yaml # Reference template

build.sh                     # Builds all 3 Docker images
deploy.sh                    # Deploys JupyterHub to K8s
```

## Ports

| Port | Service |
|------|---------|
| `30080` | JupyterHub (login + profile picker) |

## Troubleshooting

**Hub pod CrashLoopBackOff:** Check logs with `kubectl -n airflow-dev logs deploy/hub`

**User pod stuck in Pending:** Check node resources with `kubectl describe node`

**Images not found:** Run `./build.sh` first — images use `imagePullPolicy: Never`
