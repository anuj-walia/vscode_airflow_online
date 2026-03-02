#!/bin/bash
# =============================================================================
# Unified Entrypoint — Version-aware initialization for Airflow 2.x and 3.x
#
# Detects AIRFLOW_VERSION env var (set at build time) and:
#   - Initializes the DB with the correct command
#   - Creates admin user with the version-appropriate method
#   - Writes jupyter_server_config.py with the correct proxy config
#   - Starts jupyterhub-singleuser (or jupyter lab for standalone)
# =============================================================================
set -e

# Activate venv
source /opt/airflow_venv/bin/activate

# Detect major version
AIRFLOW_MAJOR=$(echo "${AIRFLOW_VERSION}" | cut -d. -f1)
echo "=== Airflow ${AIRFLOW_VERSION} (major: ${AIRFLOW_MAJOR}) ==="

# Copy scheduler_wrapper.py into AIRFLOW_HOME if not present (PVC may be empty)
if [ ! -f "${AIRFLOW_HOME}/scheduler_wrapper.py" ]; then
    cp /opt/airflow-scripts/scheduler_wrapper.py "${AIRFLOW_HOME}/scheduler_wrapper.py" 2>/dev/null || true
fi

# Copy .vscode config if not present
if [ ! -d "${AIRFLOW_HOME}/.vscode" ]; then
    cp -r /opt/airflow-scripts/.vscode "${AIRFLOW_HOME}/.vscode" 2>/dev/null || true
fi

# Ensure icons directory exists (might be hidden by PVC mount)
if [ ! -d "${AIRFLOW_HOME}/icons" ]; then
    mkdir -p "${AIRFLOW_HOME}/icons"
    echo '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><circle cx="32" cy="32" r="30" fill="#017CEE"/><text x="32" y="42" text-anchor="middle" font-size="28" font-family="Arial" fill="white" font-weight="bold">A</text></svg>' > "${AIRFLOW_HOME}/icons/airflow.svg"
    echo '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><circle cx="32" cy="32" r="30" fill="#4A4A4A"/><text x="32" y="42" text-anchor="middle" font-size="24" font-family="Arial" fill="#00D084" font-weight="bold">S</text></svg>' > "${AIRFLOW_HOME}/icons/airflow-scheduler.svg"
    echo '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="12" fill="#0078D7"/><text x="32" y="44" text-anchor="middle" font-size="26" font-family="Arial" fill="white" font-weight="bold">VS</text></svg>' > "${AIRFLOW_HOME}/icons/vscode.svg"
fi

# Ensure dags directory exists
mkdir -p "${AIRFLOW_HOME}/dags"

# =============================================================================
# DB Initialization & Admin User (version-specific)
# =============================================================================
if [ ! -f "${AIRFLOW_HOME}/airflow.db" ]; then
    echo "Initializing Airflow DB..."
    if [ "${AIRFLOW_MAJOR}" = "2" ]; then
        airflow db init
        echo "Creating Admin User (FAB auth)..."
        airflow users create \
            --username admin \
            --firstname Admin \
            --lastname User \
            --role Admin \
            --email admin@example.com \
            --password admin
    else
        airflow db migrate
    fi
fi

# Airflow 3: Always ensure SimpleAuthManager password file exists
if [ "${AIRFLOW_MAJOR}" != "2" ]; then
    echo "Creating Admin User (SimpleAuthManager)..."
    echo '{"admin": "admin"}' > "${AIRFLOW_HOME}/simple_auth_manager_passwords.json.generated"
fi

# =============================================================================
# Jupyter Server Proxy Config (version-specific)
# =============================================================================
mkdir -p /root/.jupyter

# Determine the service prefix (set by JupyterHub, defaults to / for standalone)
SERVICE_PREFIX="${JUPYTERHUB_SERVICE_PREFIX:-/}"

if [ "${AIRFLOW_MAJOR}" = "2" ]; then
    # Airflow 2: webserver proxy with absolute_url=False
    AIRFLOW_PROXY_NAME="airflow-webserver"
    AIRFLOW_PROXY_CMD="['/opt/airflow_venv/bin/airflow', 'webserver', '--port', '{port}']"
    AIRFLOW_PROXY_ABSOLUTE="False"
    AIRFLOW_PROXY_TITLE="Airflow Webserver"
else
    # Airflow 3: api-server proxy with absolute_url=True (React SPA needs full path)
    AIRFLOW_PROXY_NAME="airflow-api"
    AIRFLOW_PROXY_CMD="['/opt/airflow_venv/bin/airflow', 'api-server', '--port', '{port}']"
    AIRFLOW_PROXY_ABSOLUTE="True"
    AIRFLOW_PROXY_TITLE="Airflow API Server"
    # Set dynamic base URL for Airflow 3 React SPA
    export AIRFLOW__API__BASE_URL="http://localhost:8888${SERVICE_PREFIX}airflow-api"
fi

cat > /root/.jupyter/jupyter_server_config.py << PYEOF
c.ServerProxy.servers = {
    '${AIRFLOW_PROXY_NAME}': {
        'command': ${AIRFLOW_PROXY_CMD},
        'timeout': 120,
        'absolute_url': ${AIRFLOW_PROXY_ABSOLUTE},
        'launcher_entry': {
            'title': '${AIRFLOW_PROXY_TITLE}',
            'icon_path': '${AIRFLOW_HOME}/icons/airflow.svg'
        }
    },
    'airflow-scheduler': {
        'command': ['/opt/airflow_venv/bin/python', '${AIRFLOW_HOME}/scheduler_wrapper.py'],
        'absolute_url': False,
        'port': 8999,
        'timeout': 120,
        'launcher_entry': {
            'title': 'Airflow Scheduler Logs',
            'icon_path': '${AIRFLOW_HOME}/icons/airflow-scheduler.svg'
        }
    },
    'vscode': {
        'command': ['code-server', '--auth', 'none', '--disable-telemetry', '--port', '{port}'],
        'timeout': 300,
        'absolute_url': False,
        'launcher_entry': {
            'title': 'VS Code',
            'icon_path': '${AIRFLOW_HOME}/icons/vscode.svg'
        }
    }
}
PYEOF

echo "Proxy config written for: ${AIRFLOW_PROXY_NAME}"

# =============================================================================
# Start Jupyter
# =============================================================================
echo "Starting JupyterLab (Airflow ${AIRFLOW_VERSION})..."

# If running under JupyterHub, use jupyterhub-singleuser
if [ -n "${JUPYTERHUB_API_TOKEN}" ]; then
    exec jupyterhub-singleuser \
        --allow-root \
        --ip=0.0.0.0 \
        --port=8888 \
        --NotebookApp.token='' \
        --ServerApp.root_dir="${AIRFLOW_HOME}"
else
    # Standalone mode (docker compose up)
    exec jupyter lab \
        --ip=0.0.0.0 \
        --port=8888 \
        --no-browser \
        --allow-root \
        --NotebookApp.token=''
fi
