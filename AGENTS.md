hub # AGENTS.md — Developer Onboarding Guide

> **Last updated:** 2026-02-11  
> **Repo:** `anuj-walia/vscode_airflow_online`  
> **Branch:** `airflow3`

---

## 1. Overview & Context

- **Purpose:** Provides a single-container, batteries-included development environment for Apache Airflow 3.x, bundling JupyterLab (primary UI), VS Code (code-server), and the Airflow API Server + Scheduler behind `jupyter-server-proxy`.
- **Project Type:** Single-Service / Docker / Python 3.11 / Apache Airflow 3.1.3.
- **Mental Model:** _"One Docker container, one entrypoint — JupyterLab is the gateway that reverse-proxies into Airflow and VS Code, so the developer only connects to port 8888."_

---

## 2. Quickstart (Verified)

### Prerequisites

| Tool            | Required |
| :-------------- | :------- |
| Docker          | ✅        |
| Docker Compose  | ✅        |

### Commands

| Action    | Command                        | What to Expect                                                                                                 |
| :-------- | :----------------------------- | :------------------------------------------------------------------------------------------------------------- |
| **Build & Start** | `docker compose up --build` | Container builds (~3-5 min first time), then prints `Starting JupyterLab...`                                   |
| **Access UI**      | Open `http://localhost:8888` | JupyterLab launcher with icons for "Airflow API Server", "Airflow Scheduler Logs", and "VS Code" (code-server). |
| **Stop**           | `docker compose down`        | Graceful shutdown. Data in `./dags` and `./logs` persists via bind mounts.                                     |

### Verification Path

1. Run `docker compose up --build`.
2. Wait for `Scheduler UI ready at port 8999` in stdout — confirms the scheduler wrapper is alive.
3. Open `http://localhost:8888` → you should see the JupyterLab launcher.
4. Click the **"Airflow API Server"** launcher icon → Airflow UI should load at `http://localhost:8888/airflow-api/`.
5. Default login: **admin / admin** (via `SimpleAuthManager`).

---

## 3. Repo Map & Centers of Gravity

```
vscode_airflow_online/
├── Dockerfile              # ⭐ The single build definition — base image, deps, env vars, proxy config
├── docker-compose.yml      # ⭐ Orchestration — single service, port mapping, volume mounts
├── entrypoint.sh           # ⭐ Container boot script — DB init, user creation, JupyterLab launch
├── scheduler_wrapper.py    # ⭐ Scheduler + log-viewer HTTP server (port 8999)
├── requirements.txt        # Python dependencies (Jupyter, jupyter-server-proxy)
├── dags/                   # Airflow DAG definitions (mounted into container)
│   └── my_first_dag.py     # Example DAG with TaskFlow API (python_task → bash_task)
├── AIRFLOW3_MIGRATION.md   # Airflow 3.x migration notes
├── .vscode/                # VS Code debug configs (launch.json)
├── .gitignore              # Excludes airflow.db, logs/, plugins/
├── README.md               # User-facing documentation
└── AGENTS.md               # ← You are here
```

### The "Must-Read" List (5 files)

If you only read 5 files to understand the entire project, read these:

| Priority | File                    | Why                                                                          |
| :------- | :---------------------- | :--------------------------------------------------------------------------- |
| 1        | `Dockerfile`            | Single source of truth for the runtime environment, env vars, and proxy wiring. |
| 2        | `entrypoint.sh`         | Boot sequence: DB migrate → admin user (SimpleAuthManager) → JupyterLab. |
| 3        | `scheduler_wrapper.py`  | The only custom Python code — runs the scheduler + serves a log viewer.       |
| 4        | `docker-compose.yml`    | Volume mounts, port exposure, container restart policy.                      |
| 5        | `dags/my_first_dag.py`  | Example DAG demonstrating the TaskFlow API pattern used in this repo.         |

---

## 4. Execution Flow

### 4.1 Startup Sequence

```
docker compose up --build
  │
  ├─ 1. Docker builds image from Dockerfile
  │     ├─ Base: python:3.11-slim
  │     ├─ System deps: git, curl, nodejs, npm, build-essential, libsqlite3-dev
  │     ├─ code-server installed (VS Code in browser)
  │     ├─ Python venv at /opt/airflow_venv
  │     ├─ pip install: requirements.txt + Airflow 3.1.3 (with constraints)
  │     ├─ Airflow env vars set (SQLite, SequentialExecutor, proxy fix, API base URL)
  │     ├─ jupyter_server_config.py written with ServerProxy config
  │     ├─ VS Code extensions installed (Python, YAML, CSV Editor)
  │     └─ Scripts + .vscode/ copied into /opt/airflow/
  │
  ├─ 2. Container starts → entrypoint.sh
  │     ├─ source /opt/airflow_venv/bin/activate
  │     ├─ if airflow.db missing:
  │     │     └─ airflow db migrate (Airflow 3.x)
  │     ├─ Create admin user via SimpleAuthManager JSON file
  │     └─ exec jupyter lab --ip=0.0.0.0 --port=8888 ...
  │
  └─ 3. JupyterLab listens on :8888
        └─ jupyter-server-proxy auto-starts on first request:
              ├─ /airflow-api/ → airflow api-server --port {port}
              ├─ /airflow-scheduler/ → python scheduler_wrapper.py (port 8999)
              └─ /vscode/ → code-server --port {port}
```

### 4.2 Request Lifecycle

#### Airflow API Server Access
```
Browser → :8888/airflow-api/
  → jupyter-server-proxy reverse proxy
    → airflow api-server (dynamic port)
      → FastAPI app (Airflow 3.x UI + REST API)
        → SQLite (airflow.db)
```

#### Scheduler Access (Interactive)
```
Browser → :8888/airflow-scheduler/
  → jupyter-server-proxy reverse proxy
    → scheduler_wrapper.py :8999
      → SchedulerHandler.do_GET()
        → if scheduler not started: serves Setup Form (DAGs folder input)
        → if scheduler running: serves Log Viewer (last 100 lines, auto-refresh 5s)
      → SchedulerHandler.do_POST(/)
        → validates folder exists → starts scheduler thread → redirects to log viewer
      → SchedulerHandler.do_POST(/stop)
        → terminates scheduler → redirects to setup form
```

#### DAG Execution
```
User submits DAGs folder via Setup Form
  → scheduler_wrapper.py starts scheduler thread
    → Sets AIRFLOW__CORE__DAGS_FOLDER for the subprocess
    → Scans the user-specified DAGs folder
      → Parses DAG files (TaskFlow API)
        → SequentialExecutor runs tasks in-process
          → Results stored in SQLite
```

---

## 5. Architecture & Dependency Rules

### Component Table

| Module / File             | Responsibility                                              | Primary Consumer                     |
| :------------------------ | :---------------------------------------------------------- | :----------------------------------- |
| `Dockerfile`              | Build definition, env vars, proxy config, extensions        | `docker compose`                     |
| `docker-compose.yml`      | Service orchestration, port exposure, volume mounts         | Developer CLI                        |
| `entrypoint.sh`           | Runtime bootstrap (DB init, user creation, Jupyter launch)  | Docker ENTRYPOINT                    |
| `scheduler_wrapper.py`    | Airflow scheduler runner + HTTP log viewer                  | `jupyter-server-proxy`               |
| `requirements.txt`        | Python dependency manifest                                  | `Dockerfile` (`pip install`)         |
| `dags/`                   | Airflow DAG definitions                                     | Airflow Scheduler (DagBag scanner)   |
| `.gitignore`              | Source control exclusions                                   | Git                                  |

### Boundary Rules

- **`dags/`** should only contain Airflow DAG files (Python). They should not import from `scheduler_wrapper.py`.
- **`scheduler_wrapper.py`** is a standalone utility — it does not import from `dags/`.
- **`entrypoint.sh`** is purely a boot script — do not add application logic here.
- **Environment variables** are defined in two places: `Dockerfile` (build-time defaults) and `docker-compose.yml` (`environment:` overrides). Docker Compose values take precedence at runtime.

---

## 6. Configuration & Secrets

### Environment Variables

| Variable                                    | Defined In     | Value / Purpose                                                     |
| :------------------------------------------ | :------------- | :------------------------------------------------------------------ |
| `AIRFLOW_HOME`                              | `Dockerfile`   | `/opt/airflow` — root for Airflow config, DB, DAGs, logs.            |
| `AIRFLOW__WEBSERVER__BASE_URL`              | `Dockerfile`   | `http://localhost:8888/airflow-webserver` — proxy-aware base URL.    |
| `AIRFLOW__WEBSERVER__ENABLE_PROXY_FIX`      | `Dockerfile`   | `True` — enables proxy fix for correct URL generation.               |
| `AIRFLOW__API__BASE_URL`                    | `Dockerfile`   | `http://localhost:8888/airflow-api` — Airflow 3.x API server base URL. |
| `AIRFLOW__CORE__EXECUTOR`                   | `Dockerfile`   | `SequentialExecutor` — single-threaded, no Celery/Redis needed.      |
| `AIRFLOW__DATABASE__SQL_ALCHEMY_CONN`       | `Dockerfile`   | `sqlite:////opt/airflow/airflow.db` — local SQLite database.         |
| `AIRFLOW__CORE__LOAD_EXAMPLES`              | `Dockerfile`   | `False` — suppresses built-in example DAGs.                          |
| `LOAD_EX`                                   | `docker-compose.yml` | `n` — (redundant with above, not consumed by entrypoint).       |
| `EXECUTOR`                                  | `docker-compose.yml` | `SequentialExecutor` — (redundant, not consumed by entrypoint). |
| `PATH`                                      | `Dockerfile`   | Prepends `/opt/airflow_venv/bin` — makes venv the default shell.     |

### How to Add a New Environment Variable

1. For **Airflow config overrides**: Add `AIRFLOW__SECTION__KEY=value` to `docker-compose.yml` under `environment:`.
2. For **build-time defaults**: Add `ENV VARIABLE=value` to `Dockerfile`.
3. For **secrets / `.env` file**: Create a `.env` file in the project root and reference it in `docker-compose.yml` with `env_file: .env`. (Currently not used, but `.env` is already in `.gitignore`.)

### Credentials

| Service            | Username | Password | Location               |
| :----------------- | :------- | :------- | :--------------------- |
| Airflow API Server | admin    | admin    | `entrypoint.sh` (L17-18, via `SimpleAuthManager` JSON file) |
| JupyterLab         | _(none)_ | _(no token)_ | `entrypoint.sh` (L22, `--NotebookApp.token=''`) |

> ⚠️ **Security Note:** Both services are configured with no or trivial authentication. This is suitable only for local development.

---

## 7. Feature Catalog

| Feature                      | File(s)                         | Trigger / Access                                  |
| :--------------------------- | :------------------------------ | :------------------------------------------------ |
| Airflow API Server (UI)      | `Dockerfile`, Airflow           | `http://localhost:8888/airflow-api/`                |
| Airflow Scheduler            | `scheduler_wrapper.py`          | Auto-started by `jupyter-server-proxy` on demand  |
| Scheduler Log Viewer         | `scheduler_wrapper.py` (L24-66) | `http://localhost:8888/airflow-scheduler/`          |
| VS Code (code-server)        | `Dockerfile` (L14, L66-76)      | JupyterLab Launcher → "VS Code" icon              |
| Example DAG                  | `dags/my_first_dag.py`          | Airflow UI → DAGs tab → `my_first_dag`            |
| VS Code Debugging            | `.vscode/launch.json` (in image)| code-server → Run & Debug panel                   |
| DAG Hot-Reload               | `docker-compose.yml` (L10)      | Edit files in `./dags/` on host; scheduler picks up changes. |
| Interactive DAGs Folder      | `scheduler_wrapper.py`          | Click Scheduler icon → enter folder path → Start.           |

---

## 8. "How do I...?" (Cookbook)

### Add a New DAG

1. Create a new Python file in `./dags/`, e.g., `dags/my_new_dag.py`.
2. Use the TaskFlow API pattern (see `dags/my_first_dag.py` for reference).
3. The Airflow scheduler will auto-detect the new file within its scan interval (~30 seconds).
4. Verify in the Airflow UI at `http://localhost:8888/airflow-api/`.

### Add a Python Dependency

1. Add the package to `requirements.txt`.
2. Rebuild: `docker compose up --build`.

### Add an Airflow Provider

1. Add the provider package (e.g., `apache-airflow-providers-postgres`) to `requirements.txt`.
2. Rebuild: `docker compose up --build`.

### Add a New Environment Variable

1. Add to `docker-compose.yml` under `services.airflow-jupyter.environment`.
2. Restart: `docker compose down && docker compose up`.
3. For Airflow-specific overrides, use the `AIRFLOW__SECTION__KEY` naming convention.

### Debug a DAG in VS Code

1. Open `http://localhost:8888` → click "VS Code" in the JupyterLab launcher.
2. Open the Run & Debug panel (Ctrl+Shift+D).
3. Select a launch configuration:
   - **Python: Current File** — runs the active file directly.
   - **Airflow: Test Task** — debugs a single task (prompts for DAG ID, task ID, date).
   - **Airflow: Test DAG (CLI)** — debugs a full DAG run.
4. Set breakpoints and press F5.

### View Scheduler Logs

1. Click the "Airflow Scheduler Logs" icon in the JupyterLab launcher.
2. Alternatively, navigate to `http://localhost:8888/airflow-scheduler/`.
3. The page auto-refreshes every 5 seconds, showing the last 100 lines of `scheduler.log`.

### Access the Airflow CLI

1. Open a terminal in JupyterLab or VS Code.
2. The venv is already activated (`PATH` includes `/opt/airflow_venv/bin`).
3. Run any `airflow` command, e.g., `airflow dags list`, `airflow tasks test my_first_dag python_task 2025-01-01`.

### Persist Data After Container Restart

- **DAGs** (`./dags/`) — already mounted, persists on host.
- **Logs** (`./logs/`) — already mounted, persists on host.
- **Plugins** (`./plugins/`) — already mounted, persists on host.
- **Airflow DB** (`airflow.db`) — stored inside the container, **lost on rebuild**. Only `db migrate` re-creates it.

### Use a Custom DAGs Folder

1. Ensure the folder is mounted into the container (add a volume in `docker-compose.yml`).
2. Click the "Airflow Scheduler Logs" icon in the JupyterLab launcher.
3. In the setup form, enter the container-side path to your DAGs folder.
4. Click "Start Scheduler" — the scheduler will scan the specified folder.
5. Use "Stop & Reconfigure" in the log viewer to change the folder at any time.

---

## 9. Known Limitations & Debt

### Limitations

| Issue                                    | Impact                                                     | Mitigation / Note                                                                                      |
| :--------------------------------------- | :--------------------------------------------------------- | :----------------------------------------------------------------------------------------------------- |
| **SQLite database**                      | No concurrent writes; not suitable for parallel execution. | Intentional for local dev. Use Postgres for multi-worker setups.                                       |
| **SequentialExecutor**                   | Tasks run one at a time; no parallelism.                   | Switch to `LocalExecutor` + Postgres for parallel task execution.                                      |
| **No authentication on JupyterLab**      | Anyone on the network can access all tools.                | Token is explicitly disabled (`--NotebookApp.token=''`). Fine for localhost only.                       |
| **Trivial admin password**               | Airflow admin user uses `admin/admin`.                     | Acceptable for local dev; do not deploy to shared environments.                                        |
| **`airflow.db` not persisted**           | Database is lost on `docker compose down --volumes` / image rebuild. | Add a volume mount for `/opt/airflow/airflow.db` if persistence is desired.                        |
| **Scheduler log file grows unbounded**   | `scheduler.log` at `/opt/airflow/scheduler.log` is never rotated. | Add `logrotate` config or truncate periodically.                                                  |
| **`LOAD_EX` and `EXECUTOR` env vars unused** | Defined in `docker-compose.yml` but never consumed by `entrypoint.sh`. | Dead configuration — can be safely removed or wired into the entrypoint.                          |

### Documentation Debt

| Issue | Details |
| :---- | :------ |
| **`.vscode/launch.json` now uses `debugpy` type** | The launch configs use the `debugpy` debug type (replacing the deprecated `python` type). Ensure the `debugpy` extension is available in code-server. |
| **`icon_path` requires SVG** | The `icon_path` launcher_entry option only supports SVG files. PNG/JPEG files are silently ignored. All icons are now inline SVGs in `/opt/airflow/icons/`. |
| **VS Code configured manually** | `jupyter-vscode-proxy` was removed. VS Code (code-server) is now configured directly in `c.ServerProxy.servers` in the Dockerfile with a custom SVG icon. |

---

## High-Value Q&A

| # | Question | Answer |
| :- | :------- | :----- |
| 1 | **How do I access the Airflow UI?** | Open `http://localhost:8888/airflow-api/` in your browser; login with `admin`/`admin`. Uses Airflow 3.x `SimpleAuthManager`. |
| 2 | **How does the scheduler run?** | `scheduler_wrapper.py` serves an interactive UI on port 8999. On first visit it shows a setup form to select the DAGs folder, then starts `airflow scheduler` in a daemon thread. Launched on-demand by `jupyter-server-proxy` (configured in `Dockerfile`). |
| 3 | **Where are DAGs stored?** | In `./dags/` on the host, bind-mounted to `/opt/airflow/dags` inside the container. See `docker-compose.yml` L10. |
| 4 | **What executor is used?** | `SequentialExecutor` — tasks run one at a time in the scheduler process. Set via `AIRFLOW__CORE__EXECUTOR` in `Dockerfile` L40. |
| 5 | **How do I add a new Python dependency?** | Add it to `requirements.txt` and run `docker compose up --build`. Dependencies are installed into `/opt/airflow_venv` during the Docker build. See `Dockerfile` L23. |

---

## Safe Change Checklist

Before submitting a PR, verify these items:

- [ ] **DAG syntax:** Run `python dags/<your_dag>.py` to check for import errors.
- [ ] **Airflow DAG validation:** Run `airflow dags list` inside the container to confirm your DAG is parsed.
- [ ] **Docker build:** Run `docker compose build` to ensure no build failures.
- [ ] **Container startup:** Run `docker compose up` and verify JupyterLab loads at `http://localhost:8888`.
- [ ] **Airflow UI:** Confirm the API server is accessible at `http://localhost:8888/airflow-api/`.
- [ ] **Scheduler logs:** Confirm the scheduler log viewer loads at `http://localhost:8888/airflow-scheduler/`.
- [ ] **VS Code:** Confirm code-server launches from the JupyterLab launcher.
- [ ] **`requirements.txt`:** If you added dependencies, ensure they install cleanly during `docker compose build`.

### Files Most Likely to Need Changes

| Change Type            | Files to Touch                                           |
| :--------------------- | :------------------------------------------------------- |
| New DAG                | `dags/<new_file>.py`                                     |
| New dependency         | `requirements.txt`                                       |
| Env var change         | `docker-compose.yml`, optionally `Dockerfile`            |
| Boot behavior change   | `entrypoint.sh`                                          |
| Proxy / routing change | `Dockerfile` (jupyter_server_config.py inline block)     |
| VS Code config         | `.vscode/launch.json` (must be created/committed first)  |
