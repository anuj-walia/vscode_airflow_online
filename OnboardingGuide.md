# Onboarding Guide — Multi-User Airflow Dev Environment

> **Audience**: New developers who have never used Apache Airflow, Kubernetes, JupyterHub, or Docker.
> **Goal**: After reading this guide, you should be able to understand what every piece of this project does, how they fit together, and how to build/run/modify the system yourself.

---

## Table of Contents

1. [What Does This Project Do? (The 30-Second Pitch)](#1-what-does-this-project-do)
2. [Concepts You Need to Know](#2-concepts-you-need-to-know)
   - [Docker & Containers](#21-docker--containers)
   - [Kubernetes (K8s)](#22-kubernetes-k8s)
   - [Apache Airflow](#23-apache-airflow)
   - [JupyterHub & JupyterLab](#24-jupyterhub--jupyterlab)
   - [VS Code Server (code-server)](#25-vs-code-server-code-server)
   - [How They Work Together](#26-how-they-work-together)
3. [Project File Map — Every File Explained](#3-project-file-map--every-file-explained)
4. [Architecture Deep Dive](#4-architecture-deep-dive)
   - [The Three Docker Images](#41-the-three-docker-images)
   - [The Kubernetes Layer](#42-the-kubernetes-layer)
   - [The User Journey (End to End)](#43-the-user-journey-end-to-end)
5. [Detailed File Walkthroughs](#5-detailed-file-walkthroughs)
   - [Dockerfile (Airflow User Image)](#51-dockerfile-airflow-user-image)
   - [entrypoint.sh](#52-entrypointsh)
   - [scheduler_wrapper.py](#53-scheduler_wrapperpy)
   - [hub/Dockerfile (JupyterHub Image)](#54-hubdockerfile-jupyterhub-image)
   - [hub/jupyterhub_config.py](#55-hubjupyterhub_configpy)
   - [k8s/ Kubernetes Manifests](#56-k8s-kubernetes-manifests)
   - [build.sh](#57-buildsh)
   - [deploy.sh](#58-deploysh)
   - [launcher/ (Legacy Component)](#59-launcher-legacy-component)
   - [dags/my_first_dag.py](#510-dagsmy_first_dagpy)
   - [docker-compose.yml](#511-docker-composeyml)
6. [Step-by-Step: Building and Running the Project](#6-step-by-step-building-and-running-the-project)
7. [Day-to-Day Development Workflow](#7-day-to-day-development-workflow)
8. [How to Write Your First Airflow DAG](#8-how-to-write-your-first-airflow-dag)
9. [Common Commands Cheat Sheet](#9-common-commands-cheat-sheet)
10. [Troubleshooting & Debugging](#10-troubleshooting--debugging)
11. [Glossary](#11-glossary)

---

## 1. What Does This Project Do?

Imagine you're a team lead who wants every developer on your team to be able to:
- Write and test **Airflow data pipelines** (called "DAGs")
- Have a full **code editor** (VS Code) in their browser
- Have a **Jupyter notebook** environment
- Choose between **Airflow version 2** or **version 3**
- Do all of this **without installing anything** on their local machine except Docker

This project makes that possible. It:

1. **Packages** Airflow + JupyterLab + VS Code into a Docker container (one for each Airflow version)
2. **Uses JupyterHub** as a login portal where users sign up, pick an Airflow version, and get their own isolated environment
3. **Runs on Kubernetes** so each person gets their own "pod" (isolated container), with automatic cleanup after 30 minutes of inactivity

```
Developer opens browser → http://localhost:30080
         ↓
   JupyterHub login page
         ↓
   Signs up / Logs in
         ↓
   Chooses: "Airflow 2" or "Airflow 3"
         ↓
   Kubernetes creates a new pod just for them
         ↓
   They see JupyterLab with:
     • File browser
     • Terminal
     • VS Code (click icon)
     • Airflow Webserver (click icon)
     • Airflow Scheduler Logs (click icon)
         ↓
   After 30 min idle → pod auto-stops
```

---

## 2. Concepts You Need to Know

### 2.1 Docker & Containers

**What is Docker?**
Docker is a tool that packages an application and ALL its dependencies (operating system libraries, programs, config files) into a single unit called a **container**. Think of it like a shipping container: no matter what ship (computer) carries it, the contents are always the same.

**Key terms:**
| Term | What it means |
|------|--------------|
| **Image** | A blueprint/recipe for creating a container. Like a class in OOP. |
| **Container** | A running instance of an image. Like an object created from a class. |
| **Dockerfile** | The recipe file that tells Docker how to build an image. |
| **docker build** | The command that reads a Dockerfile and creates an image. |
| **docker run** | The command that creates and starts a container from an image. |
| **docker compose** | A tool for defining multi-container apps in a YAML file. |
| **Volume** | A way to share files between your computer and a container. |

**Example analogy:**
```
Dockerfile = Recipe for a sandwich
Image      = A frozen sandwich made from that recipe
Container  = Defrosted sandwich you're eating right now
```

**Why does this project use Docker?**
Instead of asking every developer to install Python 3.11, Airflow, JupyterLab, VS Code server, Node.js, etc., we package everything into a Docker image. One `docker build` and everyone has the exact same environment.

### 2.2 Kubernetes (K8s)

**What is Kubernetes?**
Kubernetes (often abbreviated K8s) is a system for **managing containers at scale**. If Docker is the engine of a car, Kubernetes is the fleet management system for thousands of cars.

**Why not just use Docker Compose?**
Docker Compose can run containers, but it can't:
- Automatically create a new container per user when they log in
- Monitor and kill idle containers
- Assign resource limits per user
- Restart crashed containers automatically

Kubernetes can do all of this.

**Key terms:**
| Term | What it means |
|------|--------------|
| **Cluster** | A set of machines that Kubernetes manages (for us, it's Docker Desktop's built-in K8s). |
| **Node** | One machine in the cluster (your laptop, for local dev). |
| **Pod** | The smallest deployable unit in K8s. Usually runs one container. Like `docker run`. |
| **Deployment** | Tells K8s: "Make sure X copies of this Pod are always running." |
| **Service** | A stable network endpoint to reach Pods (since Pods can restart and get new IPs). |
| **Namespace** | A virtual partition inside K8s — like a folder for organizing resources. |
| **NodePort** | A way to expose a Service outside the cluster via a fixed port on your machine. |
| **PVC** (PersistentVolumeClaim) | A request for persistent storage that survives Pod restarts. |
| **RBAC** (Role-Based Access Control) | Permissions system — e.g., "this Pod can create other Pods." |
| **ServiceAccount** | An identity given to a Pod so it can call the Kubernetes API. |
| **kubectl** | The command-line tool to talk to Kubernetes. |

**Kubernetes YAML Manifest:**
Every Kubernetes resource is defined in a YAML file. Example:
```yaml
apiVersion: v1
kind: Pod                    # What kind of resource
metadata:
  name: my-pod               # Its name
  namespace: airflow-dev     # Which namespace it lives in
spec:
  containers:
    - name: my-container
      image: python:3.11     # Which Docker image to use
      ports:
        - containerPort: 8080
```

To create this: `kubectl apply -f my-pod.yaml`
To delete this: `kubectl delete -f my-pod.yaml`
To see it running: `kubectl get pods -n airflow-dev`

### 2.3 Apache Airflow

**What is Airflow?**
Apache Airflow is a platform to **author, schedule, and monitor workflows** (data pipelines). Think of it as a sophisticated cron job manager with a web UI.

**Key terms:**
| Term | What it means |
|------|--------------|
| **DAG** (Directed Acyclic Graph) | A workflow — a collection of tasks with dependencies. Written in Python. |
| **Task** | A single unit of work in a DAG (e.g., "download file", "run SQL query"). |
| **Operator** | A template for a task (e.g., `PythonOperator`, `BashOperator`, `@task decorator`). |
| **Scheduler** | Background process that watches DAG files, figures out which tasks to run, and runs them. |
| **Webserver** | A web UI that shows your DAGs, their status, and lets you trigger them manually. |
| **Executor** | The mechanism that actually runs tasks. We use `SequentialExecutor` (one task at a time, simplest). |
| **Metadata DB** | SQLite database that stores DAG state, task status, and configuration. |
| **DAGs Folder** | The directory Airflow watches for Python files containing DAGs. Default: `/opt/airflow/dags`. |

**How Airflow works (simplified):**
```
1. You write a Python file (e.g., my_dag.py) and put it in the dags/ folder
2. The Scheduler scans the folder, parses your DAG, and registers it
3. The Scheduler triggers tasks according to the schedule you defined
4. The Webserver shows you the results in a browser UI
5. Task logs are stored in the logs/ folder
```

**Airflow 2 vs Airflow 3 — differences that matter in this project:**
| Feature | Airflow 2 | Airflow 3 |
|---------|-----------|-----------|
| Initialize DB | `airflow db init` | `airflow db migrate` |
| User creation | `airflow users create ...` | Removed — use API server |
| Web UI component | Webserver | API Server |
| CLI | Full CLI | Some subcommands removed |

### 2.4 JupyterHub & JupyterLab

**JupyterLab** is a web-based IDE for data science. It provides:
- A file browser
- Terminal access
- Notebook editing (`.ipynb` files)
- A "Launcher" where you can open new tools

**JupyterHub** is a multi-user server that:
- Provides a login page
- Spawns a separate JupyterLab instance (in its own container) for each user
- Manages authentication (username/password or OAuth)
- Automatically stops idle environments (via the Idle Culler service)

**Key terms:**
| Term | What it means |
|------|--------------|
| **KubeSpawner** | A JupyterHub plugin that creates a Kubernetes Pod for each user. |
| **NativeAuthenticator** | A JupyterHub plugin that provides username/password sign-up/login. |
| **Idle Culler** | A background service that stops pods after inactivity. |
| **jupyter-server-proxy** | A JupyterLab plugin that lets you embed other web apps (like Airflow, VS Code) inside JupyterLab. |
| **jupyterhub-singleuser** | A special JupyterLab command that integrates with JupyterHub (handles auth, base URL routing). |
| **Profile** | A named configuration option in KubeSpawner (e.g., "Airflow 2" or "Airflow 3"). |

### 2.5 VS Code Server (code-server)

**code-server** is VS Code that runs on a server and is accessed through a browser. In this project:
- It is installed inside the Airflow container
- It is accessed through JupyterLab's launcher (via `jupyter-server-proxy`)
- It comes pre-configured with:
  - Python extension
  - YAML extension
  - CSV editor extension
  - The Python interpreter pointed to the Airflow virtual environment

### 2.6 How They Work Together

```
                    ┌─────────────────────────────────────┐
                    │         YOUR BROWSER                │
                    │   http://localhost:30080             │
                    └──────────────┬──────────────────────┘
                                   │
                    ┌──────────────▼──────────────────────┐
                    │       JupyterHub (Hub Pod)          │
                    │  • Login page                       │
                    │  • Profile picker (AF2 / AF3)       │
                    │  • Idle culler service               │
                    │  • KubeSpawner                      │
                    └──────────────┬──────────────────────┘
                                   │ creates
                    ┌──────────────▼──────────────────────┐
                    │     User Pod (one per user)         │
                    │  ┌─────────────────────────────┐    │
                    │  │       JupyterLab             │    │
                    │  │  (main entry point)          │    │
                    │  │                              │    │
                    │  │  ┌──────────────────────┐    │    │
                    │  │  │ jupyter-server-proxy  │    │    │
                    │  │  └──────┬───────┬───────┘    │    │
                    │  └─────────┼───────┼────────────┘    │
                    │            │       │                  │
                    │   ┌────────▼──┐  ┌─▼──────────────┐  │
                    │   │  Airflow  │  │   VS Code      │  │
                    │   │ Webserver │  │  (code-server)  │  │
                    │   └───────────┘  └────────────────┘  │
                    │                                      │
                    │   ┌──────────────────────────────┐   │
                    │   │  Airflow Scheduler Wrapper   │   │
                    │   │  (started on demand via UI)  │   │
                    │   └──────────────────────────────┘   │
                    │                                      │
                    │   Volumes: /opt/airflow/dags          │
                    │            /opt/airflow/logs          │
                    │            /opt/airflow/plugins       │
                    └──────────────────────────────────────┘
```

**The flow:**
1. **JupyterHub** runs in its own pod, listening on port 30080
2. When a user logs in and picks a profile, JupyterHub tells **KubeSpawner** to create a new pod
3. KubeSpawner creates a pod using the `airflow-jupyter:airflow2` or `airflow-jupyter:airflow3` image
4. Inside the pod, **jupyterhub-singleuser** starts JupyterLab at the correct URL path
5. JupyterLab uses **jupyter-server-proxy** to embed Airflow Webserver and VS Code as clickable icons
6. The **Idle Culler** checks every 60 seconds; if a pod has been idle for 30 minutes, it stops it

---

## 3. Project File Map — Every File Explained

```
vscode_airflow_online/
│
├── Dockerfile                    # 🐳 Recipe for the Airflow user image
│                                 #    (Python 3.11 + Airflow + JupyterLab + VS Code)
│
├── requirements.txt              # 📦 Python packages for the Airflow image
│
├── entrypoint.sh                 # 🚀 Startup script for Docker Compose mode
│                                 #    (NOT used in Kubernetes mode)
│
├── scheduler_wrapper.py          # 🔧 Web UI for starting/stopping the Airflow Scheduler
│                                 #    Embedded in JupyterLab via server-proxy
│
├── docker-compose.yml            # 🐙 Single-user Docker Compose config (for local dev)
│
├── build.sh                      # 🏗️  Builds all 3 Docker images
│
├── deploy.sh                     # 🚢 Deploys JupyterHub to Kubernetes
│
├── dags/                         # 📂 Sample Airflow DAGs
│   └── my_first_dag.py           #    Example DAG with Python + Bash tasks
│
├── hub/                          # 📂 JupyterHub Docker image source
│   ├── Dockerfile                # 🐳 Recipe for the JupyterHub image
│   ├── jupyterhub_config.py      # ⚙️  JupyterHub configuration (KubeSpawner, auth, etc.)
│   └── requirements.txt          # 📦 Python packages for JupyterHub
│
├── k8s/                          # 📂 Kubernetes manifests (YAML files)
│   ├── namespace.yaml            # 🏷️  Creates the "airflow-dev" namespace
│   ├── hub.yaml                  # 📋 JupyterHub RBAC + PVC + Deployment + Service
│   ├── airflow-pod-template.yaml # 📄 Reference template (NOT applied directly)
│   └── launcher.yaml             # 📋 Legacy launcher (replaced by JupyterHub)
│
├── launcher/                     # 📂 Legacy Flask launcher (replaced by JupyterHub)
│   ├── Dockerfile                #    (kept for reference, not actively used)
│   ├── app.py
│   ├── requirements.txt
│   └── templates/index.html
│
├── logs/                         # 📂 Airflow log output (auto-generated)
├── plugins/                      # 📂 Airflow plugins directory (empty by default)
│
├── README.md                     # 📖 Docker Compose quickstart guide
├── README-k8s.md                 # 📖 Kubernetes deployment guide
├── DebuggingGuide.md             # 🐛 Record of every bug found and how it was fixed
├── AGENTS.md                     # 🤖 AI agent codebase documentation
└── commands.txt                  # 📝 (empty scratch file)
```

---

## 4. Architecture Deep Dive

### 4.1 The Three Docker Images

This project builds **three** Docker images. Each serves a different purpose:

#### Image 1: `airflow-jupyter:airflow2`
- **Source:** Root `Dockerfile`, built from the `main` Git branch
- **Contains:** Python 3.11, Airflow 2.11.0, JupyterLab, VS Code (code-server)
- **Purpose:** The actual development workspace for users who choose Airflow 2
- **Base image:** `python:3.11-slim`

#### Image 2: `airflow-jupyter:airflow3`
- **Source:** Root `Dockerfile`, built from the `airflow3` Git branch
- **Contains:** Same stack but with Airflow 3.1.7
- **Purpose:** The actual development workspace for users who choose Airflow 3
- **Note:** The `airflow3` branch has a different `requirements.txt` with `apache-airflow==3.1.7`

#### Image 3: `airflow-hub:latest`
- **Source:** `hub/Dockerfile`
- **Contains:** JupyterHub 5.2, KubeSpawner, NativeAuthenticator, Idle Culler
- **Purpose:** The central hub that manages logins and spawns user pods
- **Base image:** `jupyterhub/jupyterhub:5.2`

```
┌────────────────────────────────────────┐
│          build.sh orchestrates:        │
│                                        │
│  git checkout main ──→ docker build    │
│     Dockerfile         ──→ airflow-jupyter:airflow2
│                                        │
│  git checkout airflow3 ──→ docker build│
│     Dockerfile         ──→ airflow-jupyter:airflow3
│                                        │
│  hub/Dockerfile ──→ docker build       │
│                    ──→ airflow-hub:latest
└────────────────────────────────────────┘
```

### 4.2 The Kubernetes Layer

All resources live in the `airflow-dev` namespace. Here's what gets created:

```
Namespace: airflow-dev
│
├── ServiceAccount: hub-sa        ← Identity for the Hub pod (with RBAC permissions)
├── Role: hub-role                ← "hub-sa can create/delete pods, services, PVCs"
├── RoleBinding: hub-rolebinding  ← Links hub-sa to hub-role
│
├── PVC: hub-db-pvc (1Gi)        ← Persistent storage for JupyterHub's SQLite DB
│                                   (survives hub pod restarts)
│
├── Deployment: hub               ← Runs 1 replica of airflow-hub:latest
│   └── Pod: hub-xxx
│       ├── Port 8000 (proxy)     ← User-facing JupyterHub web UI
│       └── Port 8081 (hub-api)   ← Internal API (user pods talk to hub here)
│
├── Service: hub-svc
│   ├── Port 8000 → NodePort 30080  ← You access JupyterHub at localhost:30080
│   └── Port 8081                   ← Internal, for user pods to find the Hub
│
└── [Dynamic] Pods: jupyter-{username}   ← Created by KubeSpawner when users log in
    └── Container: airflow-jupyter:airflow2 or airflow3
        └── Volumes: dags, logs, plugins (hostPath mounts)
```

**Why RBAC is needed:**
The JupyterHub pod needs to call the Kubernetes API to create user pods. By default, pods can't do that. RBAC gives the hub pod permission to:
- Create, delete, list, and watch Pods
- Create and delete Services
- Access PersistentVolumeClaims
- Read pod logs

### 4.3 The User Journey (End to End)

Let's trace what happens from the moment a user opens their browser to the moment they run an Airflow DAG:

**Step 1: User visits `http://localhost:30080`**
- The browser hits NodePort 30080 on your machine
- Kubernetes routes this to port 8000 on the `hub-svc` Service
- Which forwards to port 8000 on the Hub pod
- JupyterHub serves the login page

**Step 2: User signs up or logs in**
- NativeAuthenticator handles credentials
- User state is stored in `/srv/jupyterhub/data/jupyterhub.sqlite` (on the PVC)

**Step 3: User sees the profile picker**
- JupyterHub shows two options defined in `jupyterhub_config.py`:
  - "Airflow 2.11.0 (Stable)" → `airflow-jupyter:airflow2`
  - "Airflow 3.1.7 (Latest)" → `airflow-jupyter:airflow3`

**Step 4: User clicks "Start" — KubeSpawner creates a pod**
- KubeSpawner uses the K8s API (via the `hub-sa` service account) to create a new pod
- Pod name: `jupyter-{username}` (e.g., `jupyter-anuj`)
- The pod uses `extra_container_config.command` to run a shell script that:
  1. Activates the Python virtual environment at `/opt/airflow_venv`
  2. Initializes the Airflow database (if first run)
  3. Creates an admin user for the Airflow webserver (Airflow 2 only)
  4. Starts `jupyterhub-singleuser` which is a JupyterHub-aware JupyterLab

**Step 5: JupyterLab shows the Launcher**
- The Launcher has icons for:
  - **Airflow Webserver** → runs on a dynamic port, proxied via `jupyter-server-proxy`
  - **Airflow Scheduler Logs** → runs `scheduler_wrapper.py` on port 8999
  - **VS Code** → runs `code-server` on a dynamic port

**Step 6: User writes and runs their DAG**
- Open VS Code or JupyterLab file browser
- Create/edit a `.py` file in `/opt/airflow/dags/`
- Click the "Airflow Scheduler Logs" icon → start the Scheduler → it picks up the DAG
- Click the "Airflow Webserver" icon → see the DAG in the web UI → trigger it

**Step 7: Idle cleanup**
- The Idle Culler service (running inside the Hub pod) checks every 60 seconds
- If a user pod has been idle for 30 minutes, it tells KubeSpawner to delete the pod
- The user can restart by logging in again

---

## 5. Detailed File Walkthroughs

### 5.1 Dockerfile (Airflow User Image)

**Location:** `Dockerfile` (project root)

This builds the workspace image that each user gets. Let's walk through it section by section:

```dockerfile
FROM python:3.11-slim
```
Starts from a minimal Python 3.11 image (Debian-based, ~120MB).

```dockerfile
RUN apt-get update && apt-get install -y \
    git curl nodejs npm build-essential libsqlite3-dev
```
Installs system dependencies:
- `git` — for version control inside the workspace
- `curl` — to download `code-server`
- `nodejs` / `npm` — required by `code-server`
- `build-essential` — C compiler (needed by some Python packages)
- `libsqlite3-dev` — SQLite headers (for Airflow's metadata DB)

```dockerfile
RUN curl -fsSL https://code-server.dev/install.sh | sh
```
Installs **code-server** (VS Code in the browser).

```dockerfile
RUN python -m venv /opt/airflow_venv
```
Creates a Python **virtual environment**. This isolates Airflow and its 200+ dependencies from the system Python. Everything Airflow-related is installed here.

```dockerfile
COPY requirements.txt /tmp/requirements.txt
RUN /opt/airflow_venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt
```
Installs Python packages into the venv:
- `apache-airflow==2.11.0` (or 3.1.7 on the airflow3 branch)
- `apache-airflow-providers-http` — HTTP operator for making API calls in DAGs
- `apache-airflow-providers-sqlite` — SQLite operator
- `jupyterlab` — the notebook IDE
- `jupyterhub` — single-user integration
- `jupyter-server-proxy` — lets JupyterLab embed Airflow/VS Code

```dockerfile
ENV AIRFLOW_HOME=/opt/airflow
ENV AIRFLOW__WEBSERVER__BASE_URL=http://localhost:8888/airflow-webserver
ENV AIRFLOW__WEBSERVER__ENABLE_PROXY_FIX=True
ENV AIRFLOW__CORE__EXECUTOR=SequentialExecutor
ENV AIRFLOW__DATABASE__SQL_ALCHEMY_CONN=sqlite:////opt/airflow/airflow.db
ENV AIRFLOW__CORE__LOAD_EXAMPLES=False
```
Airflow configuration via environment variables (Airflow reads `AIRFLOW__SECTION__KEY` format):
- `AIRFLOW_HOME` — where Airflow stores its data
- `BASE_URL` — tells the webserver it's running behind a proxy at `/airflow-webserver`
- `ENABLE_PROXY_FIX` — makes Airflow work correctly behind `jupyter-server-proxy`
- `SequentialExecutor` — runs one task at a time (simplest, good for dev)
- SQLite DB — lightweight, single-file database (no separate DB server needed)
- `LOAD_EXAMPLES=False` — don't load Airflow's built-in example DAGs

```dockerfile
# SVG icons for the JupyterLab launcher
RUN mkdir -p /opt/airflow/icons && \
    echo '<svg ...>A</svg>' > /opt/airflow/icons/airflow-webserver.svg && \
    echo '<svg ...>S</svg>' > /opt/airflow/icons/airflow-scheduler.svg && \
    echo '<svg ...>VS</svg>' > /opt/airflow/icons/vscode.svg
```
Creates simple SVG icons (blue circle with "A", dark circle with "S", blue square with "VS") for the JupyterLab launcher buttons.

```dockerfile
# Configure Jupyter Server Proxy
RUN mkdir -p /root/.jupyter && \
    echo "c.ServerProxy.servers = { ... }" >> /root/.jupyter/jupyter_server_config.py
```
This is the magic that makes Airflow and VS Code accessible inside JupyterLab. It configures three proxy servers:

| Server name | What it runs | Port strategy |
|------------|--------------|---------------|
| `airflow-webserver` | `airflow webserver --port {port}` | Dynamic (assigned by proxy) |
| `airflow-scheduler` | `python scheduler_wrapper.py` | Fixed: 8999 |
| `vscode` | `code-server --auth none --port {port}` | Dynamic (assigned by proxy) |

The `{port}` placeholder is filled in by `jupyter-server-proxy` at runtime.

```dockerfile
RUN code-server --install-extension ms-python.python \
    && code-server --install-extension redhat.vscode-yaml \
    && code-server --install-extension janisdd.vscode-edit-csv
```
Pre-installs VS Code extensions so they're ready the first time a user opens VS Code.

```dockerfile
ENV PATH="/opt/airflow_venv/bin:$PATH"
ENTRYPOINT ["/opt/airflow/entrypoint.sh"]
```
Makes the venv's Python the default, and sets the Docker Compose startup script.

> **Important:** In Kubernetes mode, this `ENTRYPOINT` is **overridden** by `extra_container_config.command` in `jupyterhub_config.py`. The `entrypoint.sh` is only used in Docker Compose mode.

### 5.2 entrypoint.sh

**Location:** `entrypoint.sh` (project root)

This is the startup script for **Docker Compose mode only** (not used in Kubernetes).

```bash
#!/bin/bash
set -e                                    # Exit on any error

source /opt/airflow_venv/bin/activate     # Activate the Python virtual environment

if [ ! -f "/opt/airflow/airflow.db" ]; then    # If DB doesn't exist yet...
    airflow db init                            # Initialize the Airflow metadata DB
    airflow users create \                     # Create a default admin user
        --username admin --password admin \
        --firstname Admin --lastname User \
        --role Admin --email admin@example.com
fi

exec jupyter lab \                        # Start JupyterLab on port 8888
    --ip=0.0.0.0 --port=8888 \
    --no-browser --allow-root \
    --NotebookApp.token=''                # No authentication token
```

**Why `exec`?** The `exec` command replaces the shell process with JupyterLab. This means JupyterLab becomes PID 1 in the container, so Docker's stop/restart signals go directly to JupyterLab.

### 5.3 scheduler_wrapper.py

**Location:** `scheduler_wrapper.py` (project root)

This is a custom web application that provides a browser UI for starting and managing the Airflow Scheduler.

**Why does this exist?**
The Airflow Scheduler is a background process. You can't just run it in a terminal inside the container because:
1. The JupyterLab process is already the main process
2. You want a friendly UI to start/stop the scheduler
3. You want to see scheduler logs in the browser

**How it works:**

```
Port 8999 ← jupyter-server-proxy routes here

┌────────────────────────────────┐
│  scheduler_wrapper.py          │
│                                │
│  GET / → Is scheduler running? │
│    YES → Show log page         │
│     NO → Show setup form       │
│                                │
│  POST / → Start scheduler     │
│    1. Validate DAGs folder     │
│    2. Start `airflow scheduler`│
│       in background thread     │
│    3. Redirect to log page     │
│                                │
│  POST /stop → Stop scheduler  │
│    1. Terminate subprocess     │
│    2. Redirect to setup form   │
└────────────────────────────────┘
```

**Key code explained:**

- `run_scheduler(dags_folder)` — Starts `airflow scheduler` as a subprocess, redirecting output to `/opt/airflow/scheduler.log`
- `stop_scheduler()` — Sends `SIGTERM` to the scheduler process, waits 10s, then `SIGKILL` if needed
- `SchedulerHandler` — An HTTP request handler (using Python's `http.server`) that serves two HTML pages:
  - **Setup Page:** A form where you enter the DAGs folder path and click "Start Scheduler"
  - **Log Page:** Shows the last 100 lines of `scheduler.log`, auto-refreshes every 5 seconds, and has a "Stop & Reconfigure" button

### 5.4 hub/Dockerfile (JupyterHub Image)

**Location:** `hub/Dockerfile`

This is much simpler than the Airflow Dockerfile. It extends the official JupyterHub image:

```dockerfile
FROM jupyterhub/jupyterhub:5.2            # Official JupyterHub image

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt   # Install KubeSpawner, etc.

COPY jupyterhub_config.py /srv/jupyterhub/jupyterhub_config.py  # Our config

WORKDIR /srv/jupyterhub
EXPOSE 8000

CMD ["jupyterhub", "-f", "/srv/jupyterhub/jupyterhub_config.py"]
```

The `requirements.txt` installs:
- `jupyterhub==5.2.1` — the hub itself
- `jupyterhub-kubespawner==7.0.0` — creates K8s pods for users
- `jupyterhub-nativeauthenticator==1.3.0` — username/password auth
- `jupyterhub-idle-culler==1.4.0` — stops idle pods
- `oauthenticator==17.1.0` — GitHub/Google OAuth (available but not currently used)

### 5.5 hub/jupyterhub_config.py

**Location:** `hub/jupyterhub_config.py`

This is the **brain** of the multi-user system. Let's break down each section:

#### Authentication
```python
c.JupyterHub.authenticator_class = "nativeauthenticator.NativeAuthenticator"
c.NativeAuthenticator.open_signup = True           # Users can self-register
c.NativeAuthenticator.minimum_password_length = 4  # Min password length
c.Authenticator.allow_all = True                   # Any registered user can log in
```

#### Spawner (KubeSpawner)
```python
c.JupyterHub.spawner_class = "kubespawner.KubeSpawner"
c.KubeSpawner.namespace = "airflow-dev"       # Where user pods are created
c.KubeSpawner.image_pull_policy = "Never"     # Use local Docker images only
```

#### Profile List (Version Picker)
```python
c.KubeSpawner.profile_list = [
    {
        "display_name": "Airflow 2.11.0 (Stable)",
        "slug": "airflow2",
        "kubespawner_override": {"image": "airflow-jupyter:airflow2"},
    },
    {
        "display_name": "Airflow 3.1.7 (Latest)",
        "slug": "airflow3",
        "kubespawner_override": {"image": "airflow-jupyter:airflow3"},
    },
]
```
This generates a dropdown in the JupyterHub UI. The `kubespawner_override` changes which Docker image is used based on the user's selection.

#### Pod Startup Command
```python
c.KubeSpawner.extra_container_config = {
    "command": ["/bin/bash", "-c",
        "source /opt/airflow_venv/bin/activate && "
        "if [ ! -f /opt/airflow/airflow.db ]; then "
        "  (airflow db init 2>/dev/null || airflow db migrate) && "
        "  (airflow users create ... 2>/dev/null || true); "
        "fi && "
        "exec jupyterhub-singleuser --allow-root --ip=0.0.0.0 --port=8888"
    ],
}
```
**Why `extra_container_config` instead of `cmd`?**
Our Docker image uses `ENTRYPOINT` (not `CMD`). In Kubernetes:
- `command` overrides Docker's `ENTRYPOINT`
- `args` overrides Docker's `CMD`
- KubeSpawner's `cmd` setting maps to K8s `args`, NOT `command`

So we use `extra_container_config.command` to override the `ENTRYPOINT`.

**Why the fallback logic?**
- `(airflow db init 2>/dev/null || airflow db migrate)` — Airflow 2 uses `db init`, Airflow 3 uses `db migrate`. The `||` means "if the first fails, try the second."
- `(airflow users create ... 2>/dev/null || true)` — Airflow 3 removed the `users` CLI. `|| true` ensures this failure doesn't crash the pod.

#### Resource Limits
```python
c.KubeSpawner.cpu_limit = 2           # Max 2 CPU cores per user
c.KubeSpawner.cpu_guarantee = 0.5     # Guaranteed 0.5 CPU cores
c.KubeSpawner.mem_limit = "4G"        # Max 4GB RAM per user
c.KubeSpawner.mem_guarantee = "1G"    # Guaranteed 1GB RAM
```

#### Volume Mounts
```python
c.KubeSpawner.volumes = [
    {"name": "dags",    "hostPath": {"path": "/opt/airflow-shared/dags"}},
    {"name": "logs",    "hostPath": {"path": "/opt/airflow-shared/logs"}},
    {"name": "plugins", "hostPath": {"path": "/opt/airflow-shared/plugins"}},
]
```
These use `hostPath` volumes, which mount a directory from the Kubernetes node (your laptop) into the pod. This means:
- DAGs, logs, and plugins are shared across all user pods
- They survive pod restarts

#### Idle Culler
```python
c.JupyterHub.services = [{
    "name": "idle-culler",
    "command": [
        "python3", "-m", "jupyterhub_idle_culler",
        "--timeout=1800",      # 30 minutes
        "--cull-every=60",     # Check every 60 seconds
    ],
}]
```
The idle culler runs as a JupyterHub service with RBAC roles that allow it to list users, check activity, and delete servers.

#### Hub Networking
```python
c.JupyterHub.ip = "0.0.0.0"
c.JupyterHub.port = 8000                    # User-facing proxy
c.JupyterHub.hub_bind_url = "http://0.0.0.0:8081"   # Hub API listens here
c.JupyterHub.hub_connect_url = (                     # User pods reach Hub here
    "http://hub-svc.airflow-dev.svc.cluster.local:8081"
)
```
**Two separate URLs are critical:**
- `hub_bind_url` — the address the Hub binds to (must be `0.0.0.0`, a local address)
- `hub_connect_url` — the address user pods use to find the Hub (must be the K8s Service DNS name)

If you set both to the DNS name, the Hub tries to bind to a DNS name and crashes.

### 5.6 k8s/ Kubernetes Manifests

#### k8s/namespace.yaml
```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: airflow-dev
```
Creates an isolated namespace. All resources go here. To clean up everything: `kubectl delete namespace airflow-dev`.

#### k8s/hub.yaml
This single file contains **four** Kubernetes resources (separated by `---`):

1. **ServiceAccount `hub-sa`** — An identity for the Hub pod
2. **Role `hub-role`** — Permissions to manage pods, services, PVCs, events
3. **RoleBinding `hub-rolebinding`** — Links the ServiceAccount to the Role
4. **PVC `hub-db-pvc`** — 1GB of persistent storage for JupyterHub's SQLite database
5. **Deployment `hub`** — Runs one replica of `airflow-hub:latest` with environment variables and volume mounts
6. **Service `hub-svc`** — NodePort service exposing port 30080 (proxy) and 8081 (hub API)

#### k8s/airflow-pod-template.yaml
This is a **reference document only** — it is NOT applied to Kubernetes. It documents what an Airflow workspace pod looks like when KubeSpawner creates one.

#### k8s/launcher.yaml (Legacy)
This was used before JupyterHub was set up. It defined a simpler Flask-based launcher. It's kept for reference but no longer deployed.

### 5.7 build.sh

**Location:** `build.sh` (project root)

This script builds all three Docker images:

```bash
# Step 1: Save current branch, stash changes
git stash --include-untracked

# Step 2: Switch to main branch, build Airflow 2 image
git checkout main
docker build -t airflow-jupyter:airflow2 .

# Step 3: Switch to airflow3 branch, build Airflow 3 image
git checkout airflow3
docker build -t airflow-jupyter:airflow3 .

# Step 4: Switch back, restore stash
git checkout $CURRENT_BRANCH
git stash pop

# Step 5: Build JupyterHub image
docker build -t airflow-hub:latest ./hub/
```

**Important details:**
- The script creates a placeholder `.vscode/launch.json` if it doesn't exist (the Dockerfile `COPY`s it)
- It uses a `trap cleanup EXIT` to restore your original branch even if the script fails
- First build takes ~5-10 minutes; subsequent builds are faster due to Docker layer caching

### 5.8 deploy.sh

**Location:** `deploy.sh` (project root)

This script deploys JupyterHub to Kubernetes:

```bash
# 1. Verify kubectl and cluster are available
kubectl cluster-info

# 2. Create namespace
kubectl apply -f k8s/namespace.yaml

# 3. Deploy Hub (RBAC + PVC + Deployment + Service)
kubectl apply -f k8s/hub.yaml

# 4. Wait for the Hub pod to be ready
kubectl -n airflow-dev rollout status deployment/hub --timeout=120s

# 5. Print access URL
echo "Open JupyterHub: http://localhost:30080"
```

### 5.9 launcher/ (Legacy Component)

The `launcher/` directory contains a **Flask web application** that was the original v1 approach before JupyterHub was adopted. It's simpler but single-user:

- `app.py` — Flask app with routes to create/delete/check Airflow pods via the Kubernetes API
- `templates/index.html` — A polished version picker UI
- Uses the Kubernetes Python client to create pods directly

**Why it was replaced:** The launcher only supports one workspace at a time (one pod). JupyterHub supports multiple users, each with their own pod, plus authentication, idle cleanup, and profile-based image selection.

### 5.10 dags/my_first_dag.py

**Location:** `dags/my_first_dag.py`

This is a minimal example DAG to help new users understand the basics:

```python
from airflow.decorators import dag, task
from datetime import datetime

@dag(start_date=datetime(2025, 11, 1), schedule=None, catchup=False)
def my_first_dag():

    @task                        # A Python task
    def python_task():
        print("just some task")
        return "Hello from python task"

    @task.bash                   # A Bash task
    def bash_task(msg):
        print("executing bash task")
        return f"echo {msg}"

    bash_task(python_task())     # python_task runs FIRST, passes result to bash_task

my_first_dag()                  # This line registers the DAG with Airflow
```

**Key concepts demonstrated:**
- `@dag` decorator — defines a DAG with a start date and no automatic schedule
- `@task` decorator — wraps a Python function as an Airflow task
- `@task.bash` decorator — wraps a function that returns a bash command
- `catchup=False` — don't run the DAG for past dates
- Task dependency: `bash_task(python_task())` means python_task must complete first

### 5.11 docker-compose.yml

**Location:** `docker-compose.yml` (project root)

For quick single-user local development (no Kubernetes needed):

```yaml
services:
  airflow-jupyter:
    build: .                              # Build from Dockerfile
    image: airflow-jupyter-vscode
    ports:
      - "8888:8888"                       # JupyterLab on localhost:8888
    volumes:
      - ./dags:/opt/airflow/dags          # Mount your dags folder
      - ./logs:/opt/airflow/logs          # Mount logs for persistence
      - ./plugins:/opt/airflow/plugins    # Mount plugins
      - .:/opt/airflow/workspace          # Mount entire project for VS Code
    environment:
      - LOAD_EX=n
      - EXECUTOR=SequentialExecutor
    restart: unless-stopped
```

Usage: `docker compose up --build` → Open `http://localhost:8888`

---

## 6. Step-by-Step: Building and Running the Project

### Prerequisites

1. **Docker Desktop** (or OrbStack) installed and running
2. **Kubernetes enabled** in Docker Desktop settings (Settings → Kubernetes → Enable Kubernetes)
3. **kubectl** installed (`brew install kubectl` on Mac)
4. **Git** installed

Verify:
```bash
docker info          # Should show Docker version
kubectl cluster-info # Should show cluster running
git --version        # Should show git version
```

### Option A: Multi-User Mode (Kubernetes)

```bash
# 1. Clone the repo
git clone <repo-url> && cd vscode_airflow_online

# 2. Build all 3 Docker images (~10 min first time)
./build.sh

# 3. Deploy JupyterHub to Kubernetes
./deploy.sh

# 4. Open in browser
open http://localhost:30080

# 5. First time: Click "Sign Up", create a username and password
# 6. Log in with your credentials
# 7. Select "Airflow 2.11.0" or "Airflow 3.1.7"
# 8. Click "Start" — wait ~30-60 seconds
# 9. You'll be redirected to your JupyterLab workspace!
```

### Option B: Single-User Mode (Docker Compose)

```bash
# 1. Clone the repo
git clone <repo-url> && cd vscode_airflow_online

# 2. Build and start
docker compose up --build

# 3. Open in browser
open http://localhost:8888

# That's it! JupyterLab is running with Airflow, VS Code, etc.
```

### Teardown

```bash
# Kubernetes mode:
kubectl delete namespace airflow-dev    # Removes everything

# Docker Compose mode:
docker compose down                     # Stops and removes containers
docker compose down -v                  # Also removes volumes
```

---

## 7. Day-to-Day Development Workflow

### Writing and Testing DAGs

1. **Open your workspace** (via JupyterHub or Docker Compose)
2. **Open VS Code** by clicking the "VS Code" icon in the JupyterLab Launcher
3. **Create a new DAG file** in `/opt/airflow/dags/`, e.g. `my_dag.py`
4. **Start the Scheduler:**
   - Click "Airflow Scheduler Logs" in the JupyterLab Launcher
   - Enter the DAGs folder path (default is fine)
   - Click "Start Scheduler"
5. **Open the Airflow Webserver:**
   - Click "Airflow Webserver" in the JupyterLab Launcher
   - Your DAG should appear in the list (may take ~30 seconds for the scheduler to parse it)
6. **Trigger the DAG:**
   - Toggle the DAG "on" (click the switch next to its name)
   - Click "Trigger DAG" (the play button)
7. **Check results:**
   - Click on the DAG run → Task Instance → Logs

### Modifying the Environment

If you need to change the Airflow container:
1. Edit the `Dockerfile`, `requirements.txt`, or `entrypoint.sh`
2. Rebuild: `docker build -t airflow-jupyter:airflow2 .`
3. For Kubernetes: delete any existing user pods, then log in again to get a new pod

If you need to change JupyterHub behavior:
1. Edit `hub/jupyterhub_config.py`
2. Rebuild: `docker build -t airflow-hub:latest ./hub/`
3. Restart: `kubectl -n airflow-dev rollout restart deployment/hub`

---

## 8. How to Write Your First Airflow DAG

### Structure of a DAG File

```python
from airflow.decorators import dag, task
from datetime import datetime

@dag(
    dag_id="my_pipeline",          # Unique name (optional, defaults to function name)
    start_date=datetime(2025, 1, 1),  # When the DAG becomes active
    schedule="@daily",             # How often to run (None = manual only)
    catchup=False,                 # Don't backfill past dates
    tags=["example"],              # Tags for filtering in the UI
)
def my_pipeline():
    """A simple example pipeline."""

    @task
    def extract():
        """Simulate extracting data."""
        data = {"users": 100, "revenue": 50000}
        return data

    @task
    def transform(raw_data: dict):
        """Simulate transforming data."""
        raw_data["avg_revenue"] = raw_data["revenue"] / raw_data["users"]
        return raw_data

    @task
    def load(processed_data: dict):
        """Simulate loading data."""
        print(f"Loading data: {processed_data}")

    # Define the pipeline: extract → transform → load
    raw = extract()
    processed = transform(raw)
    load(processed)

my_pipeline()  # Register the DAG
```

### Common Schedule Options
| Value | Meaning |
|-------|---------|
| `None` | Manual trigger only |
| `"@daily"` | Once per day at midnight |
| `"@hourly"` | Once per hour |
| `"@weekly"` | Once per week |
| `"0 6 * * *"` | Every day at 6:00 AM (cron syntax) |

### Important Rules
1. **Every DAG file must call the function at the end** (e.g., `my_pipeline()`) — otherwise Airflow won't detect it
2. **Don't put heavy logic at the module level** — Airflow parses DAG files frequently; expensive code outside tasks slows things down
3. **Use `@task` for Python code, `@task.bash` for shell commands**
4. **Files must be in the DAGs folder** (`/opt/airflow/dags/`)

---

## 9. Common Commands Cheat Sheet

### Kubernetes Basics

```bash
# See all pods in the airflow-dev namespace
kubectl -n airflow-dev get pods

# See pods with more detail (node, IP, etc.)
kubectl -n airflow-dev get pods -o wide

# See all resources in the namespace
kubectl -n airflow-dev get all

# View JupyterHub logs (last 50 lines)
kubectl -n airflow-dev logs deploy/hub --tail=50

# Follow JupyterHub logs in real time
kubectl -n airflow-dev logs -f deploy/hub

# View a user pod's logs
kubectl -n airflow-dev logs jupyter-<username> --tail=50

# View logs from a crashed pod (previous instance)
kubectl -n airflow-dev logs jupyter-<username> --previous

# Full details of a pod (events, env vars, volumes, etc.)
kubectl -n airflow-dev describe pod jupyter-<username>

# See recent events sorted by time
kubectl -n airflow-dev get events --sort-by='.lastTimestamp' | tail -20

# Delete a stuck user pod
kubectl -n airflow-dev delete pod jupyter-<username>

# Restart JupyterHub (to pick up config changes)
kubectl -n airflow-dev rollout restart deployment/hub

# Tear down everything
kubectl delete namespace airflow-dev
```

### Docker Basics

```bash
# Build an image
docker build -t <image-name>:<tag> <path-to-dockerfile-dir>

# List local images
docker images | grep airflow

# Run a shell in an image (for debugging)
docker run --rm -it --entrypoint /bin/bash airflow-jupyter:airflow2

# Check what ENTRYPOINT and CMD an image has
docker inspect airflow-jupyter:airflow2 \
  --format='Entrypoint: {{json .Config.Entrypoint}} | CMD: {{json .Config.Cmd}}'

# Docker Compose start
docker compose up --build

# Docker Compose stop
docker compose down
```

### Airflow CLI (inside a container)

```bash
# Initialize or migrate the database
airflow db init      # Airflow 2
airflow db migrate   # Airflow 3

# List all detected DAGs
airflow dags list

# Trigger a DAG run manually
airflow dags trigger my_first_dag

# Test a single task (without the scheduler)
airflow tasks test my_first_dag python_task 2025-01-01

# List registered users (Airflow 2 only)
airflow users list

# Check Airflow version
airflow version
```

---

## 10. Troubleshooting & Debugging

### Problem: `http://localhost:30080` shows nothing

**Possible causes:**
1. Kubernetes is not enabled in Docker Desktop
2. The Hub pod isn't running

**Steps:**
```bash
kubectl cluster-info                    # Is the cluster running?
kubectl -n airflow-dev get pods         # Is the hub pod Running?
kubectl -n airflow-dev logs deploy/hub  # Any errors in the logs?
```

### Problem: Hub pod is in `CrashLoopBackOff`

```bash
kubectl -n airflow-dev logs deploy/hub --tail=50   # Check error
```

Common causes:
- **Port conflict:** Another service is using NodePort 30080. Check with `kubectl -n airflow-dev get svc`
- **Config error:** Syntax mistake in `jupyterhub_config.py`. Fix config, rebuild, redeploy

### Problem: User pod stuck in `Pending`

```bash
kubectl -n airflow-dev describe pod jupyter-<username>
```

Check the "Events" section at the bottom. Common causes:
- **Insufficient resources:** Your machine doesn't have enough CPU/RAM. Reduce limits in `jupyterhub_config.py`
- **Image not found:** Run `./build.sh` first. Images use `imagePullPolicy: Never` (no registry pull)

### Problem: 404 error after pod starts

This means JupyterLab is running at the wrong URL path. The fix was applied via `extra_container_config.command` in `jupyterhub_config.py` — make sure it's using `jupyterhub-singleuser`, not plain `jupyter lab`.

### Problem: Airflow 3 pod crashes immediately

Check logs:
```bash
kubectl -n airflow-dev logs jupyter-<username> --previous
```

Likely cause: The startup command uses Airflow 2-specific CLI commands that don't exist in Airflow 3. The fix is already applied (fallback logic), but if you modified the startup command, ensure you have:
```bash
(airflow db init 2>/dev/null || airflow db migrate)   # Works for both versions
(airflow users create ... 2>/dev/null || true)        # Silently skips in AF3
```

### Problem: Changes to config aren't taking effect

After editing `hub/jupyterhub_config.py`:
```bash
docker build -t airflow-hub:latest ./hub/                  # Rebuild image
kubectl -n airflow-dev rollout restart deployment/hub       # Restart with new image
```

After editing the main `Dockerfile`:
```bash
docker build -t airflow-jupyter:airflow2 .                 # Rebuild
kubectl -n airflow-dev delete pod jupyter-<username>        # Delete old pod
# Log in again to JupyterHub to get a new pod with the updated image
```

---

## 11. Glossary

| Term | Definition |
|------|-----------|
| **Airflow** | Apache Airflow — a workflow orchestration platform for scheduling and monitoring data pipelines. |
| **API Server** | Airflow 3's replacement for the Webserver. Serves the UI and REST API. |
| **CrashLoopBackOff** | A Kubernetes pod status meaning the container keeps crashing and K8s keeps restarting it with increasing delays. |
| **DAG** | Directed Acyclic Graph — Airflow's term for a workflow (a collection of tasks with dependencies). |
| **Deployment** | A Kubernetes resource that manages a set of identical pods, ensuring the desired number are always running. |
| **Docker Image** | An immutable snapshot of a filesystem plus metadata (OS, libraries, app code) that can be run as a container. |
| **Executor** | Airflow's mechanism for running tasks. `SequentialExecutor` runs one task at a time (for dev). |
| **hostPath** | A Kubernetes volume type that mounts a directory from the node's filesystem into a pod. |
| **Idle Culler** | A JupyterHub service that monitors user activity and stops pods after a configurable timeout. |
| **jupyterhub-singleuser** | A JupyterHub-aware version of JupyterLab that integrates with the Hub's routing and authentication. |
| **jupyter-server-proxy** | A JupyterLab extension that lets you embed other web applications (like Airflow or VS Code) inside JupyterLab. |
| **KubeSpawner** | A JupyterHub plugin that creates a Kubernetes Pod for each user, rather than a local process. |
| **kubectl** | The command-line tool for interacting with Kubernetes clusters. |
| **Namespace** | A Kubernetes virtual partition for organizing and isolating resources. |
| **NativeAuthenticator** | A JupyterHub plugin with a simple username/password sign-up and login system. |
| **NodePort** | A Kubernetes Service type that opens a specific port on every node in the cluster. |
| **Pod** | The smallest deployable unit in Kubernetes — usually runs a single container. |
| **PVC** | PersistentVolumeClaim — a request for storage that persists beyond pod lifetime. |
| **RBAC** | Role-Based Access Control — Kubernetes' permission system. |
| **SequentialExecutor** | Airflow executor that runs one task at a time. Simplest option, suitable for development. |
| **ServiceAccount** | An identity assigned to a pod, used for authenticating K8s API calls. |
| **Virtual Environment (venv)** | An isolated Python environment with its own installed packages. |
| **Webserver** | Airflow's web UI component (Airflow 2). In Airflow 3, it's called the API Server. |

---

> **📖 Further Reading:**
> - [DebuggingGuide.md](DebuggingGuide.md) — Detailed record of every bug encountered (with exact fix steps)
> - [README.md](README.md) — Quick start guide for Docker Compose mode
> - [README-k8s.md](README-k8s.md) — Quick start guide for Kubernetes mode
