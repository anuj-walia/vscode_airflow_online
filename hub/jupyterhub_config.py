"""
JupyterHub Configuration — Multi-User Airflow Dev Environment

Uses KubeSpawner to create per-user pods with a profile picker
(Airflow 2 or Airflow 3). Includes idle culling after 30 minutes.
"""

import os

# =============================================================================
# Authentication — NativeAuthenticator (self-registration)
# =============================================================================
c.JupyterHub.authenticator_class = "nativeauthenticator.NativeAuthenticator"

# Allow users to self-register (sign-up page)
c.NativeAuthenticator.open_signup = True

# First registered user becomes admin
c.NativeAuthenticator.admin_users = {"anuj"}

# Optionally set a minimum password length
c.NativeAuthenticator.minimum_password_length = 4

# Allow any authenticated user to access the Hub
c.Authenticator.allow_all = True

# =============================================================================
# Spawner — KubeSpawner with Profile List
# =============================================================================
c.JupyterHub.spawner_class = "kubespawner.KubeSpawner"

# Namespace where user pods are created
c.KubeSpawner.namespace = os.environ.get("K8S_NAMESPACE", "airflow-dev")

# Use local images only (no registry pull)
c.KubeSpawner.image_pull_policy = "Never"

# Profile list — users choose Airflow version at spawn time
c.KubeSpawner.profile_list = [
    {
        "display_name": "Airflow 2.11.0 (Stable)",
        "slug": "airflow2",
        "description": "Apache Airflow 2.x with Webserver UI, JupyterLab, and VS Code",
        "kubespawner_override": {
            "image": "airflow-jupyter:airflow2",
        },
    },
    {
        "display_name": "Airflow 3.1.7 (Latest)",
        "slug": "airflow3",
        "description": "Apache Airflow 3.x with API Server UI, JupyterLab, and VS Code",
        "kubespawner_override": {
            "image": "airflow-jupyter:airflow3",
        },
    },
]

# Default image (fallback)
c.KubeSpawner.image = "airflow-jupyter:airflow2"

# Container port — JupyterLab runs on 8888 inside the Airflow images
c.KubeSpawner.port = 8888

# Override the Docker ENTRYPOINT via extra_container_config.
# KubeSpawner.cmd maps to K8s "args" (which overrides Docker CMD),
# but our Airflow images use ENTRYPOINT, not CMD. To override ENTRYPOINT,
# we must set the K8s "command" field directly via extra_container_config.
#
# This shell script:
#   1. Activates the Python venv (where Airflow + JupyterLab are installed)
#   2. Initializes the Airflow DB and creates admin user (if first run)
#   3. Starts jupyterhub-singleuser (which respects JUPYTERHUB_SERVICE_PREFIX
#      so JupyterLab serves at /user/{name}/ instead of /)
c.KubeSpawner.cmd = []  # Clear default cmd
c.KubeSpawner.extra_container_config = {
    "command": [
        "/bin/bash", "-c",
        "source /opt/airflow_venv/bin/activate && "
        "if [ ! -f /opt/airflow/airflow.db ]; then "
        "  (airflow db init 2>/dev/null || airflow db migrate) && "
        "  (airflow users create "
        "    --username admin --firstname Admin --lastname User "
        "    --role Admin --email admin@example.com --password admin "
        "    2>/dev/null || true); "
        "fi && "
        "echo '{\"admin\": \"admin\"}' > /opt/airflow/simple_auth_manager_passwords.json.generated && "
        "export AIRFLOW__API__BASE_URL=http://localhost:8888${JUPYTERHUB_SERVICE_PREFIX}airflow-api && "
        "exec jupyterhub-singleuser "
        "  --allow-root "
        "  --ip=0.0.0.0 "
        "  --port=8888 "
        "  --NotebookApp.token='' "
        "  --ServerApp.root_dir=/opt/airflow"
    ],
}

# Environment variables passed to user pods
c.KubeSpawner.environment = {
    "LOAD_EX": "n",
    "EXECUTOR": "SequentialExecutor",
    "PATH": "/opt/airflow_venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
}

# Resource limits per user pod
c.KubeSpawner.cpu_limit = 2
c.KubeSpawner.cpu_guarantee = 0.5
c.KubeSpawner.mem_limit = "4G"
c.KubeSpawner.mem_guarantee = "1G"

# Volume mounts for shared DAGs, logs, plugins
# Each user gets their own subdirectory under the shared paths
c.KubeSpawner.volumes = [
    {
        "name": "dags",
        "hostPath": {"path": "/opt/airflow-shared/dags", "type": "DirectoryOrCreate"},
    },
    {
        "name": "logs",
        "hostPath": {"path": "/opt/airflow-shared/logs", "type": "DirectoryOrCreate"},
    },
    {
        "name": "plugins",
        "hostPath": {"path": "/opt/airflow-shared/plugins", "type": "DirectoryOrCreate"},
    },
]

c.KubeSpawner.volume_mounts = [
    {"name": "dags", "mountPath": "/opt/airflow/dags"},
    {"name": "logs", "mountPath": "/opt/airflow/logs"},
    {"name": "plugins", "mountPath": "/opt/airflow/plugins"},
]

# Pod startup timeout (Airflow images are large, may take time)
c.KubeSpawner.start_timeout = 300
c.KubeSpawner.http_timeout = 120

# =============================================================================
# Idle Culler — Automatically stop inactive pods
# =============================================================================
c.JupyterHub.services = [
    {
        "name": "idle-culler",
        "command": [
            "python3",
            "-m",
            "jupyterhub_idle_culler",
            "--timeout=1800",       # 30 minutes of inactivity
            "--cull-every=60",      # Check every 60 seconds
            "--max-age=0",          # No max age (only cull on inactivity)
            "--concurrency=5",
        ],
    }
]

# Grant the idle-culler service permission to manage servers
c.JupyterHub.load_roles = [
    {
        "name": "idle-culler",
        "scopes": [
            "list:users",
            "read:users:activity",
            "read:servers",
            "delete:servers",
        ],
        "services": ["idle-culler"],
    }
]

# =============================================================================
# Hub Configuration
# =============================================================================

# Bind the proxy (user-facing) to all interfaces on port 8000
c.JupyterHub.ip = "0.0.0.0"
c.JupyterHub.port = 8000

# Bind the Hub API (internal) to 0.0.0.0:8081
# This is where the proxy and spawned pods connect to the Hub
c.JupyterHub.hub_bind_url = "http://0.0.0.0:8081"

# URL that spawned user pods use to reach the Hub API
# Uses the K8s service DNS name so pods can find the hub
c.JupyterHub.hub_connect_url = os.environ.get(
    "HUB_CONNECT_URL", "http://hub-svc.airflow-dev.svc.cluster.local:8081"
)

# Allow named servers (users can run multiple environments)
c.JupyterHub.allow_named_servers = False

# Database for user state (mounted via PVC in K8s)
c.JupyterHub.db_url = "sqlite:////srv/jupyterhub/data/jupyterhub.sqlite"

# Shutdown user pods when hub restarts
c.JupyterHub.cleanup_servers = False

# Logging
c.JupyterHub.log_level = "INFO"
