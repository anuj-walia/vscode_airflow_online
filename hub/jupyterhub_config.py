"""
JupyterHub Configuration — Multi-User Airflow Dev Environment

Single image architecture: users select Airflow version + Python version
from dropdowns. Airflow is installed at first pod startup and cached in PVC.
"""

import os

# =============================================================================
# Supported Versions (static list)
# =============================================================================
AIRFLOW_VERSIONS = {
    "2.9.3":  {"display_name": "Airflow 2.9.3"},
    "2.10.5": {"display_name": "Airflow 2.10.5"},
    "2.11.0": {"display_name": "Airflow 2.11.0 (Stable)", "default": True},
    "3.0.1":  {"display_name": "Airflow 3.0.1"},
    "3.1.7":  {"display_name": "Airflow 3.1.7 (Latest)"},
}

PYTHON_VERSIONS = {
    "3.9":  {"display_name": "Python 3.9"},
    "3.10": {"display_name": "Python 3.10"},
    "3.11": {"display_name": "Python 3.11", "default": True},
    "3.12": {"display_name": "Python 3.12"},
}

# =============================================================================
# Authentication — NativeAuthenticator (self-registration)
# =============================================================================
c.JupyterHub.authenticator_class = "nativeauthenticator.NativeAuthenticator"
c.NativeAuthenticator.open_signup = True
c.NativeAuthenticator.admin_users = {"anuj"}
c.NativeAuthenticator.minimum_password_length = 4
c.Authenticator.allow_all = True

# =============================================================================
# Spawner — KubeSpawner with Version Dropdowns
# =============================================================================
c.JupyterHub.spawner_class = "kubespawner.KubeSpawner"
c.KubeSpawner.namespace = os.environ.get("K8S_NAMESPACE", "airflow-dev")
c.KubeSpawner.image_pull_policy = "Never"

# --- Single image for all versions ---
c.KubeSpawner.image = "airflow-jupyter:latest"

# --- Profile with dropdown options ---
c.KubeSpawner.profile_list = [
    {
        "display_name": "Airflow Dev Environment",
        "slug": "airflow",
        "description": "Select your Airflow and Python versions below",
        "profile_options": {
            "airflow_version": {
                "display_name": "Airflow Version",
                "choices": AIRFLOW_VERSIONS,
            },
            "python_version": {
                "display_name": "Python Version",
                "choices": PYTHON_VERSIONS,
            },
        },
    },
]

# --- Pre-spawn hook: pass version selections as env vars ---
async def pre_spawn_hook(spawner):
    """Pass the selected Airflow and Python versions to the pod as env vars."""
    options = spawner.user_options

    af_ver = options.get("profile--airflow_version", "2.11.0")
    py_ver = options.get("profile--python_version", "3.11")

    spawner.log.info(f"Spawning pod: Airflow {af_ver} / Python {py_ver}")

    # Pass to pod as env vars (entrypoint.sh reads these)
    spawner.environment["AIRFLOW_VERSION"] = af_ver
    spawner.environment["PYTHON_VERSION"] = py_ver

c.KubeSpawner.pre_spawn_hook = pre_spawn_hook

# Container port
c.KubeSpawner.port = 8888

# --- Startup: use the smart entrypoint ---
c.KubeSpawner.cmd = []
c.KubeSpawner.extra_container_config = {
    "command": ["/opt/airflow-scripts/entrypoint.sh"],
}

# Environment variables passed to user pods
c.KubeSpawner.environment = {
    "PATH": "/opt/jupyter_venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
}

# Resource limits per user pod
c.KubeSpawner.cpu_limit = 2
c.KubeSpawner.cpu_guarantee = 0.5
c.KubeSpawner.mem_limit = "4G"
c.KubeSpawner.mem_guarantee = "1G"

# =============================================================================
# Per-User PVC — Persistent Workspace & Database
# =============================================================================
c.KubeSpawner.storage_pvc_ensure = True
c.KubeSpawner.storage_capacity = "5Gi"
c.KubeSpawner.storage_access_modes = ["ReadWriteOnce"]
c.KubeSpawner.storage_pvc_name_template = "claim-{username}"
c.KubeSpawner.storage_mount_path = "/opt/airflow"

# Pod startup timeout (first start installs Airflow, needs extra time)
c.KubeSpawner.start_timeout = 600
c.KubeSpawner.http_timeout = 300

# =============================================================================
# Idle Culler
# =============================================================================
c.JupyterHub.services = [
    {
        "name": "idle-culler",
        "command": [
            "python3", "-m", "jupyterhub_idle_culler",
            "--timeout=1800", "--cull-every=60", "--max-age=0", "--concurrency=5",
        ],
    }
]

c.JupyterHub.load_roles = [
    {
        "name": "idle-culler",
        "scopes": ["list:users", "read:users:activity", "read:servers", "delete:servers"],
        "services": ["idle-culler"],
    }
]

# =============================================================================
# Hub Configuration
# =============================================================================
c.JupyterHub.ip = "0.0.0.0"
c.JupyterHub.port = 8000
c.JupyterHub.hub_bind_url = "http://0.0.0.0:8081"
c.JupyterHub.hub_connect_url = os.environ.get(
    "HUB_CONNECT_URL", "http://hub-svc.airflow-dev.svc.cluster.local:8081"
)
c.JupyterHub.allow_named_servers = False
c.JupyterHub.db_url = "sqlite:////srv/jupyterhub/data/jupyterhub.sqlite"
c.JupyterHub.cleanup_servers = False
c.JupyterHub.log_level = "INFO"
