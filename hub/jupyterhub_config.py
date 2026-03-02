"""
JupyterHub Configuration — Multi-User Airflow Dev Environment

Uses KubeSpawner with profile_options to let users select Airflow version
and Python version from dropdowns. Per-user PVCs persist workspace/database.
"""

import os

# =============================================================================
# Supported Versions — Must match build.sh VERSIONS array
# =============================================================================
SUPPORTED_VERSIONS = [
    {"airflow": "2.10.5", "python": "3.11"},
    {"airflow": "2.11.0", "python": "3.11"},
    {"airflow": "3.0.1",  "python": "3.11"},
    {"airflow": "3.1.7",  "python": "3.11"},
    {"airflow": "3.1.7",  "python": "3.12"},
]

# Build lookup: (airflow_ver, python_ver) -> True
VALID_COMBOS = {(v["airflow"], v["python"]) for v in SUPPORTED_VERSIONS}

# Unique lists for dropdowns
AIRFLOW_VERSIONS = sorted(set(v["airflow"] for v in SUPPORTED_VERSIONS))
PYTHON_VERSIONS = sorted(set(v["python"] for v in SUPPORTED_VERSIONS))

# =============================================================================
# Authentication — NativeAuthenticator (self-registration)
# =============================================================================
c.JupyterHub.authenticator_class = "nativeauthenticator.NativeAuthenticator"

# Allow users to self-register (sign-up page)
c.NativeAuthenticator.open_signup = True

# Static admin user
c.NativeAuthenticator.admin_users = {"anuj"}

# Optionally set a minimum password length
c.NativeAuthenticator.minimum_password_length = 4

# Allow any authenticated user to access the Hub
c.Authenticator.allow_all = True

# =============================================================================
# Spawner — KubeSpawner with Version Dropdowns
# =============================================================================
c.JupyterHub.spawner_class = "kubespawner.KubeSpawner"

# Namespace where user pods are created
c.KubeSpawner.namespace = os.environ.get("K8S_NAMESPACE", "airflow-dev")

# Use local images only (no registry pull)
c.KubeSpawner.image_pull_policy = "Never"

# --- Profile with dropdown options ---
# Build Airflow version choices
airflow_choices = {}
for ver in AIRFLOW_VERSIONS:
    major = ver.split(".")[0]
    label = f"Airflow {ver}"
    if ver == "2.11.0":
        label += " (Stable)"
    elif ver == "3.1.7":
        label += " (Latest)"
    airflow_choices[ver] = {
        "display_name": label,
        **({"default": True} if ver == "2.11.0" else {}),
    }

# Build Python version choices
python_choices = {}
for ver in PYTHON_VERSIONS:
    python_choices[ver] = {
        "display_name": f"Python {ver}",
        **({"default": True} if ver == "3.11" else {}),
    }

c.KubeSpawner.profile_list = [
    {
        "display_name": "Airflow Dev Environment",
        "slug": "airflow",
        "description": "Apache Airflow with JupyterLab, VS Code, and Scheduler",
        "profile_options": {
            "airflow_version": {
                "display_name": "Airflow Version",
                "choices": airflow_choices,
            },
            "python_version": {
                "display_name": "Python Version",
                "choices": python_choices,
            },
        },
    },
]

# Default image (fallback)
c.KubeSpawner.image = "airflow-jupyter:2.11.0-py3.11"

# --- Pre-spawn hook: construct image tag from selections ---
async def pre_spawn_hook(spawner):
    """Construct the Docker image tag from the selected version options."""
    options = spawner.user_options

    af_ver = options.get("profile--airflow_version", "2.11.0")
    py_ver = options.get("profile--python_version", "3.11")

    # Validate combo exists
    if (af_ver, py_ver) not in VALID_COMBOS:
        # Fall back to nearest valid combo for this Airflow version
        valid_py = [v["python"] for v in SUPPORTED_VERSIONS if v["airflow"] == af_ver]
        if valid_py:
            py_ver = valid_py[0]
            spawner.log.warning(
                f"Invalid combo {af_ver}/py{py_ver}, falling back to {af_ver}/py{valid_py[0]}"
            )
        else:
            af_ver, py_ver = "2.11.0", "3.11"
            spawner.log.warning(f"Invalid combo, using default 2.11.0/py3.11")

    image = f"airflow-jupyter:{af_ver}-py{py_ver}"
    spawner.image = image
    spawner.log.info(f"Selected image: {image}")

    # Pass version info as env vars to the pod
    spawner.environment["AIRFLOW_VERSION"] = af_ver
    spawner.environment["PYTHON_VERSION"] = py_ver

c.KubeSpawner.pre_spawn_hook = pre_spawn_hook

# Container port — JupyterLab runs on 8888 inside the Airflow images
c.KubeSpawner.port = 8888

# --- Startup command: use the smart entrypoint ---
# The Dockerfile's ENTRYPOINT is /opt/airflow-scripts/entrypoint.sh
# which handles all version-specific initialization.
# We override it here to ensure it runs correctly under JupyterHub.
c.KubeSpawner.cmd = []  # Clear default cmd
c.KubeSpawner.extra_container_config = {
    "command": ["/opt/airflow-scripts/entrypoint.sh"],
}

# Environment variables passed to user pods
c.KubeSpawner.environment = {
    "PATH": "/opt/airflow_venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
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

# PVC name template: claim-{username}
c.KubeSpawner.storage_pvc_name_template = "claim-{username}"

# Mount the PVC at AIRFLOW_HOME so workspace, DB, DAGs, logs all persist
c.KubeSpawner.storage_mount_path = "/opt/airflow"

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
c.JupyterHub.hub_bind_url = "http://0.0.0.0:8081"

# URL that spawned user pods use to reach the Hub API
c.JupyterHub.hub_connect_url = os.environ.get(
    "HUB_CONNECT_URL", "http://hub-svc.airflow-dev.svc.cluster.local:8081"
)

# Single server per user
c.JupyterHub.allow_named_servers = False

# Database for user state (mounted via PVC in K8s)
c.JupyterHub.db_url = "sqlite:////srv/jupyterhub/data/jupyterhub.sqlite"

# Keep user pods when hub restarts
c.JupyterHub.cleanup_servers = False

# Logging
c.JupyterHub.log_level = "INFO"
