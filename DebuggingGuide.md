# JupyterHub on Kubernetes — Debugging Guide

This document records every issue encountered while deploying JupyterHub with KubeSpawner on a local Kubernetes cluster (OrbStack), along with the exact debugging steps, commands, and fixes applied.

---

## Issue 1: Port 30080 Conflict — Old Launcher Service

### Symptom
`kubectl apply -f k8s/hub.yaml` failed because port `30080` was already in use by the old Flask launcher's `NodePort` service.

### Debugging Commands

```bash
# List all services in the namespace to find what's using port 30080
kubectl -n airflow-dev get svc
```

**Why this works:** `get svc` shows all Kubernetes Services with their type, ports, and NodePort mappings. This revealed `launcher-svc` was still bound to port `30080`.

### Fix

```bash
# Delete old launcher resources (service, deployment, RBAC)
kubectl -n airflow-dev delete svc launcher-svc airflow-workspace-svc
kubectl -n airflow-dev delete deploy launcher
kubectl -n airflow-dev delete sa launcher-sa
kubectl -n airflow-dev delete role launcher-role
kubectl -n airflow-dev delete rolebinding launcher-rolebinding

# Re-apply the hub manifests
kubectl apply -f k8s/hub.yaml
```

**Key insight:** When replacing one application with another on the same NodePort, you must clean up the old Service first. Kubernetes won't allow two NodePort Services to claim the same port.

---

## Issue 2: Hub Pod CrashLoopBackOff — Binding to K8s DNS Name

### Symptom
The hub pod entered `CrashLoopBackOff` immediately after creation.

### Debugging Commands

```bash
# Step 1: Check pod status
kubectl -n airflow-dev get pods
# Output: hub-xxx  0/1  CrashLoopBackOff  5 (47s ago)  4m7s

# Step 2: Read the pod logs to find the error
kubectl -n airflow-dev logs deploy/hub --tail=50
```

**Why `logs deploy/hub`:** Instead of finding the exact pod name, you can use `deploy/hub` to read logs from the deployment's current pod. The `--tail=50` limits output to the last 50 lines, focusing on the most recent crash.

### Error Found

```
Failed to bind hub to http://hub-svc.airflow-dev.svc.cluster.local:8000/hub/
socket.gaierror: [Errno -2] Name or service not known
```

### Root Cause
JupyterHub has **two separate concepts** that look similar:

| Setting | Purpose | Maps to |
|---------|---------|---------|
| `hub_bind_url` | Where the Hub **API listens** (bind address) | `0.0.0.0:8081` |
| `hub_connect_url` | How spawned **pods reach** the Hub API | K8s service DNS |

We only set `hub_connect_url`, which JupyterHub also tried to use as its **bind address**. It attempted to bind to `hub-svc.airflow-dev.svc.cluster.local`, which the pod can't bind to (it's a DNS name pointing to the Service ClusterIP, not the pod's own IP).

### Fix
Separate bind address from connect URL in `jupyterhub_config.py`:

```python
# Bind the Hub API to all interfaces inside the pod
c.JupyterHub.hub_bind_url = "http://0.0.0.0:8081"

# URL that spawned user pods use to reach the Hub
c.JupyterHub.hub_connect_url = "http://hub-svc.airflow-dev.svc.cluster.local:8081"
```

Also updated `k8s/hub.yaml` to expose port 8081 on both the container and the Service:

```yaml
ports:
  - containerPort: 8000    # Proxy (user-facing)
    name: proxy
  - containerPort: 8081    # Hub API (internal)
    name: hub-api
```

### Rebuild & Redeploy Cycle

```bash
# 1. Rebuild the hub Docker image (picks up config changes)
docker build -t airflow-hub:latest ./hub/

# 2. Apply updated K8s manifests
kubectl apply -f k8s/hub.yaml

# 3. Restart the deployment to pick up the new image
kubectl -n airflow-dev rollout restart deployment/hub

# 4. Wait and verify
sleep 15
kubectl -n airflow-dev get pods
kubectl -n airflow-dev logs deploy/hub --tail=10
```

**Why `rollout restart`:** Since we use `imagePullPolicy: Never` with local images, Kubernetes won't notice that `airflow-hub:latest` changed. `rollout restart` forces a new pod with the latest image.

---

## Issue 3: User Pod 404 — JupyterLab Serving at Wrong Base URL

### Symptom
After logging in and spawning a pod, the browser shows:
```
Jupyter Server — 404 : Not Found
You are requesting a page that does not exist!
```
at URL `localhost:30080/user/anuj`.

### Debugging Commands

```bash
# Step 1: Verify the user pod is running
kubectl -n airflow-dev get pods
# Output: jupyter-anuj  1/1  Running

# Step 2: Read the user pod logs to see what JupyterLab is doing
kubectl -n airflow-dev logs -l heritage=jupyterhub --tail=50

# Step 3: Check what environment variables KubeSpawner injected
kubectl -n airflow-dev describe pod jupyter-anuj | grep -E "(JUPYTERHUB|JPY)"
```

**Why `-l heritage=jupyterhub`:** KubeSpawner labels all user pods with `heritage=jupyterhub`. Using a label selector reads logs from all user pods without needing exact pod names.

**Why `describe pod | grep`:** The `describe` command shows the full pod spec including environment variables. Filtering for `JUPYTERHUB` reveals the service prefix, API URLs, and routing configuration.

### Error Found
The user pod logs showed:
```
Jupyter Server is running at: http://jupyter-anuj:8888/lab    ← serving at /lab
404 GET /user/anuj                                            ← but Hub routes to /user/anuj/
```

JupyterHub routes requests to `/user/anuj/`, but JupyterLab was serving at root `/`. The `JUPYTERHUB_SERVICE_PREFIX=/user/anuj/` env var was injected but JupyterLab wasn't reading it.

### Root Cause
Our Airflow images' `entrypoint.sh` starts plain `jupyter lab`, which ignores JupyterHub env vars. The correct approach is to start `jupyterhub-singleuser`, which is a JupyterHub-aware wrapper that:
- Reads `JUPYTERHUB_SERVICE_PREFIX` and sets the correct `base_url`
- Notifies the Hub when the server is ready
- Handles OAuth for Hub authentication

### Sub-Issue: KubeSpawner.cmd vs Docker ENTRYPOINT

First attempt was to set `c.KubeSpawner.cmd = ["jupyterhub-singleuser"]`, but the original `entrypoint.sh` still ran.

```bash
# Verify what command the pod actually ran
kubectl -n airflow-dev get pod jupyter-anuj \
  -o jsonpath='{.spec.containers[0].command}' && echo ""
# Output: (empty)

kubectl -n airflow-dev get pod jupyter-anuj \
  -o jsonpath='{.spec.containers[0].args}' && echo ""
# Output: ["jupyterhub-singleuser","--allow-root","--ip=0.0.0.0"]
```

**Key discovery:** `KubeSpawner.cmd` maps to Kubernetes `args`, NOT `command`:

| Kubernetes field | Docker equivalent | KubeSpawner setting |
|-----------------|-------------------|---------------------|
| `command` | Overrides `ENTRYPOINT` | `extra_container_config.command` |
| `args` | Overrides `CMD` | `cmd` |

Since our Dockerfile uses `ENTRYPOINT ["/opt/airflow/entrypoint.sh"]` (not `CMD`), setting `args` had no effect — the entrypoint.sh ran and ignored the args.

```bash
# Verify the Docker image uses ENTRYPOINT
docker inspect airflow-jupyter:airflow2 \
  --format='{{json .Config.Entrypoint}}:::{{json .Config.Cmd}}'
# Output: ["/opt/airflow/entrypoint.sh"]:::null
```

### Fix
Use `extra_container_config` to set the K8s `command` field directly:

```python
c.KubeSpawner.cmd = []  # Clear default
c.KubeSpawner.extra_container_config = {
    "command": [
        "/bin/bash", "-c",
        "source /opt/airflow_venv/bin/activate && "
        "... && "
        "exec jupyterhub-singleuser --allow-root --ip=0.0.0.0 --port=8888"
    ],
}
```

---

## Issue 4: Airflow 3 Pod CrashLoopBackOff — Removed CLI Commands

### Symptom
Airflow 2 pod works fine. Airflow 3 pod crashes within 3 seconds of starting with `Back-off restarting failed container`.

### Debugging Commands

```bash
# Step 1: Get previous crash logs (pod may still exist in CrashLoopBackOff)
kubectl -n airflow-dev logs jupyter-testaf3 --previous

# If pod was already cleaned up, use events to see what happened
kubectl -n airflow-dev get events --sort-by='.lastTimestamp' | grep testaf3
```

**Why `--previous`:** When a container crashes and restarts, `--previous` shows logs from the **last crashed instance**, not the currently starting one. This is critical for debugging CrashLoopBackOff.

```bash
# Step 2: Verify the binary exists in the Airflow 3 image
docker run --rm --entrypoint /bin/bash airflow-jupyter:airflow3 -c \
  "source /opt/airflow_venv/bin/activate && which jupyterhub-singleuser"
# Output: /opt/airflow_venv/bin/jupyterhub-singleuser  ← exists
```

**Why `--entrypoint /bin/bash`:** Overrides the Docker ENTRYPOINT so we can run arbitrary commands in the container for inspection.

```bash
# Step 3: Reproduce the exact crash locally
docker run --rm --entrypoint /bin/bash airflow-jupyter:airflow3 -c \
  "source /opt/airflow_venv/bin/activate && airflow db init"
# Output: error: invalid choice: 'init' (choose from 'check', 'migrate', ...)
```

### Errors Found (Two Issues)

**Issue 4a: `airflow db init` removed in Airflow 3**
```
argument COMMAND: invalid choice: 'init' (choose from 'check', 'migrate', ...)
```
Airflow 3 replaced `db init` with `db migrate`. The `db init` call fails with exit code 2.

**Issue 4b: `airflow users` command removed in Airflow 3**
```
argument GROUP_OR_COMMAND: invalid choice: 'users' (choose from 'api-server', 'dags', ...)
```
Airflow 3 removed the entire `users` CLI subcommand. User management now happens through the API server.

Both failures occur before `jupyterhub-singleuser` starts because we used `&&` chaining, so any failure aborts the entire command.

### Fix
Make both operations fail-safe:

```python
# DB init: try Airflow 2's `db init`, fall back to Airflow 3's `db migrate`
"(airflow db init 2>/dev/null || airflow db migrate) && "

# User creation: skip gracefully if command doesn't exist (Airflow 3)
"(airflow users create ... 2>/dev/null || true); "
```

**Why `2>/dev/null || alternative`:** Suppresses stderr from the failing command, then uses `||` to run the fallback. For user creation, `|| true` ensures the exit code is always 0, so the `&&` chain continues.

---

## General Debugging Toolkit for JupyterHub + KubeSpawner

### Pod & Log Commands

```bash
# Overview of all pods
kubectl -n airflow-dev get pods -o wide

# Hub logs (recent)
kubectl -n airflow-dev logs deploy/hub --tail=50

# User pod logs (by label — catches all user pods)
kubectl -n airflow-dev logs -l heritage=jupyterhub --tail=50

# Crashed pod logs (previous instance)
kubectl -n airflow-dev logs <pod-name> --previous

# Full pod details including env vars, events, mounts
kubectl -n airflow-dev describe pod <pod-name>

# Recent events sorted by time
kubectl -n airflow-dev get events --sort-by='.lastTimestamp' | tail -20
```

### Image Inspection

```bash
# Check ENTRYPOINT vs CMD
docker inspect <image> --format='{{json .Config.Entrypoint}}:::{{json .Config.Cmd}}'

# Run arbitrary commands inside an image (bypass entrypoint)
docker run --rm --entrypoint /bin/bash <image> -c "<command>"

# Check if a binary exists
docker run --rm --entrypoint /bin/bash <image> -c "which <binary>"
```

### Rebuild & Redeploy Cycle

```bash
# Full cycle for config changes
docker build -t airflow-hub:latest ./hub/       # Rebuild image
kubectl -n airflow-dev delete pod <user-pod>     # Kill stale user pods
kubectl -n airflow-dev rollout restart deploy/hub # Restart hub with new image
sleep 15                                         # Wait for startup
kubectl -n airflow-dev get pods                  # Verify status
kubectl -n airflow-dev logs deploy/hub --tail=10 # Check for errors
```

### Verifying Connectivity

```bash
# Check if the hub is serving HTTP
curl -s -o /dev/null -w "%{http_code}" http://localhost:30080/hub/login
# Expected: 200

# Check service endpoints
kubectl -n airflow-dev get svc
kubectl -n airflow-dev get endpoints
```

---

## Summary of All Fixes Applied

| # | Issue | Root Cause | Fix |
|---|-------|-----------|-----|
| 1 | Port conflict | Old `launcher-svc` using 30080 | Delete old K8s resources |
| 2 | Hub CrashLoop | Binding to DNS name instead of `0.0.0.0` | Use `hub_bind_url` separate from `hub_connect_url` |
| 3 | User 404 | JupyterLab at `/` instead of `/user/{name}/` | Override ENTRYPOINT via `extra_container_config.command` to run `jupyterhub-singleuser` |
| 3a | cmd override ignored | `KubeSpawner.cmd` sets K8s `args` not `command` | Use `extra_container_config.command` to override Docker `ENTRYPOINT` |
| 4a | AF3 pod crash | `airflow db init` removed in Airflow 3 | `(airflow db init \|\| airflow db migrate)` |
| 4b | AF3 pod crash | `airflow users` removed in Airflow 3 | `(airflow users create ... \|\| true)` |

### Key Takeaways

1. **Always check `kubectl logs --previous`** for CrashLoopBackOff pods — the current instance may not have any logs yet.
2. **K8s `command` ≠ `args`**: `command` overrides Docker `ENTRYPOINT`, `args` overrides Docker `CMD`. KubeSpawner's `cmd` field maps to `args`, which is surprising.
3. **Test locally with `docker run --entrypoint`** before deploying to K8s — it's much faster than the rebuild+redeploy cycle.
4. **Airflow 2 vs 3 CLI differences** are significant: `db init` → `db migrate`, `users` command removed entirely.
5. **Use `|| true`** for optional commands in shell chains to prevent cascading failures.
