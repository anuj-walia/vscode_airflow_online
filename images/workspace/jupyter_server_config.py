"""
jupyter-server-proxy registration for the Airflow 3 workspace.

Installed at ${VIRTUAL_ENV}/etc/jupyter/jupyter_server_config.py so it is
image content, readable by the non-root runtime user, and unaffected by the
per-user PVC that is mounted over $AIRFLOW_HOME.

Every service here binds to loopback. The only listener exposed outside the
pod is jupyterhub-singleuser on 8888, so reaching Airflow or VS Code requires
a JupyterHub OAuth session for that specific user.
"""

import os

AIRFLOW_HOME = os.environ.get("AIRFLOW_HOME", "/opt/airflow")
ICONS = "/srv/workspace/icons"
VENV_BIN = "/opt/venv/bin"

c.ServerProxy.servers = {
    # ---------------------------------------------------------------------
    # Airflow API server (UI + REST API + Execution API).
    #
    # The port is fixed rather than proxy-assigned because task workers reach
    # the Execution API directly on it, bypassing the proxy. See
    # bin/start.sh for why that direct route is required.
    # ---------------------------------------------------------------------
    "airflow-api": {
        "command": [
            f"{VENV_BIN}/airflow",
            "api-server",
            "--host",
            "127.0.0.1",
            "--port",
            "9091",
        ],
        "port": 9091,
        "timeout": 180,
        # Airflow builds absolute redirects from [api] base_url, so the proxy
        # must not rewrite them back to relative paths.
        "absolute_url": True,
        "launcher_entry": {
            "title": "Airflow UI",
            "icon_path": f"{ICONS}/airflow-api.svg",
        },
    },
    # ---------------------------------------------------------------------
    # Supervisor for scheduler / dag-processor / triggerer, with a log viewer.
    # ---------------------------------------------------------------------
    "airflow-scheduler": {
        "command": [
            f"{VENV_BIN}/python",
            "/srv/workspace/bin/workspace_services.py",
        ],
        "port": 8999,
        "timeout": 120,
        "absolute_url": False,
        "launcher_entry": {
            "title": "Airflow Services",
            "icon_path": f"{ICONS}/airflow-scheduler.svg",
        },
    },
    # ---------------------------------------------------------------------
    # code-server. --auth none is safe only because it listens on loopback
    # behind the authenticated proxy; never publish this port.
    # ---------------------------------------------------------------------
    "vscode": {
        "command": [
            "code-server",
            "--auth",
            "none",
            "--bind-addr",
            "127.0.0.1:{port}",
            "--disable-telemetry",
            "--disable-update-check",
            AIRFLOW_HOME,
        ],
        "timeout": 300,
        "absolute_url": False,
        "launcher_entry": {
            "title": "VS Code",
            "icon_path": f"{ICONS}/vscode.svg",
        },
    },
}
