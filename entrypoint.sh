#!/bin/bash
# =============================================================================
# Unified Entrypoint — Single Image, Runtime Airflow Installation
#
# On first pod startup:
#   1. Creates a Python venv with the user-selected Python version
#   2. Installs Apache Airflow with constraints
#   3. Initializes the DB and creates admin user
#   4. Writes version-specific Jupyter proxy config
#
# On subsequent startups:
#   Skips installation (venv is cached in PVC), goes straight to init + launch.
# =============================================================================
set -e

# ---- Config from env vars (set by JupyterHub profile_options) ----
AIRFLOW_VERSION="${AIRFLOW_VERSION:-2.11.0}"
PYTHON_VERSION="${PYTHON_VERSION:-3.11}"
AIRFLOW_MAJOR=$(echo "${AIRFLOW_VERSION}" | cut -d. -f1)

echo "=============================================="
echo "  Airflow ${AIRFLOW_VERSION} / Python ${PYTHON_VERSION}"
echo "=============================================="

# ---- Paths ----
AIRFLOW_VENV="/opt/airflow/venv"
JUPYTER_VENV="/opt/jupyter_venv"
SCRIPTS_DIR="/opt/airflow-scripts"

# ---- Step 1: Install Airflow (or reinstall if version changed) ----
NEED_INSTALL=false

if [ ! -f "${AIRFLOW_VENV}/bin/airflow" ]; then
    NEED_INSTALL=true
    echo ""
    echo "▸ First startup: installing Apache Airflow ${AIRFLOW_VERSION}..."
elif [ -f "/opt/airflow/.airflow_version" ] && [ -f "/opt/airflow/.python_version" ]; then
    CACHED_AF_VER=$(cat /opt/airflow/.airflow_version)
    CACHED_PY_VER=$(cat /opt/airflow/.python_version)
    if [ "${CACHED_AF_VER}" != "${AIRFLOW_VERSION}" ] || [ "${CACHED_PY_VER}" != "${PYTHON_VERSION}" ]; then
        echo ""
        echo "▸ Version changed: ${CACHED_AF_VER}/py${CACHED_PY_VER} → ${AIRFLOW_VERSION}/py${PYTHON_VERSION}"
        echo "  Removing old installation..."
        rm -rf "${AIRFLOW_VENV}"
        rm -f /opt/airflow/airflow.db
        NEED_INSTALL=true
    fi
fi

if [ "${NEED_INSTALL}" = true ]; then
    echo "  (This takes 1-3 minutes. Subsequent starts will be instant.)"
    echo ""

    # Create venv with selected Python version
    PYTHON_BIN="python${PYTHON_VERSION}"
    if ! command -v "${PYTHON_BIN}" &>/dev/null; then
        echo "❌ Python ${PYTHON_VERSION} not found. Available versions:"
        ls /usr/bin/python3.* 2>/dev/null || true
        exit 1
    fi

    echo "  Creating venv with ${PYTHON_BIN}..."
    "${PYTHON_BIN}" -m venv "${AIRFLOW_VENV}"

    echo "  Installing apache-airflow==${AIRFLOW_VERSION}..."
    CONSTRAINT_URL="https://raw.githubusercontent.com/apache/airflow/constraints-${AIRFLOW_VERSION}/constraints-${PYTHON_VERSION}.txt"
    "${AIRFLOW_VENV}/bin/pip" install --no-cache-dir \
        "apache-airflow==${AIRFLOW_VERSION}" \
        --constraint "${CONSTRAINT_URL}"

    echo "  ✓ Airflow ${AIRFLOW_VERSION} installed"
    echo ""

    # Save version info for subsequent starts
    echo "${AIRFLOW_VERSION}" > /opt/airflow/.airflow_version
    echo "${PYTHON_VERSION}" > /opt/airflow/.python_version
else
    echo "  ✓ Airflow ${AIRFLOW_VERSION} already installed (cached in PVC)"
fi

# ---- Ensure Airflow venv is in PATH ----
export PATH="${AIRFLOW_VENV}/bin:${JUPYTER_VENV}/bin:${PATH}"

# ---- Copy scripts into AIRFLOW_HOME if not present (PVC may be empty) ----
if [ ! -f "${AIRFLOW_HOME}/scheduler_wrapper.py" ]; then
    cp "${SCRIPTS_DIR}/scheduler_wrapper.py" "${AIRFLOW_HOME}/scheduler_wrapper.py" 2>/dev/null || true
fi
if [ ! -d "${AIRFLOW_HOME}/.vscode" ]; then
    cp -r "${SCRIPTS_DIR}/.vscode" "${AIRFLOW_HOME}/.vscode" 2>/dev/null || true
fi

# ---- Ensure directories exist ----
mkdir -p "${AIRFLOW_HOME}/dags" "${AIRFLOW_HOME}/logs" "${AIRFLOW_HOME}/plugins"

# ---- Create icons if missing (hidden by PVC on first mount) ----
if [ ! -d "${AIRFLOW_HOME}/icons" ]; then
    mkdir -p "${AIRFLOW_HOME}/icons"
    echo '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><circle cx="32" cy="32" r="30" fill="#017CEE"/><text x="32" y="42" text-anchor="middle" font-size="28" font-family="Arial" fill="white" font-weight="bold">A</text></svg>' > "${AIRFLOW_HOME}/icons/airflow.svg"
    echo '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><circle cx="32" cy="32" r="30" fill="#4A4A4A"/><text x="32" y="42" text-anchor="middle" font-size="24" font-family="Arial" fill="#00D084" font-weight="bold">S</text></svg>' > "${AIRFLOW_HOME}/icons/airflow-scheduler.svg"
    echo '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="12" fill="#0078D7"/><text x="32" y="44" text-anchor="middle" font-size="26" font-family="Arial" fill="white" font-weight="bold">VS</text></svg>' > "${AIRFLOW_HOME}/icons/vscode.svg"
fi

# =============================================================================
# Step 2: DB Initialization & Admin User (version-specific)
# =============================================================================
if [ ! -f "${AIRFLOW_HOME}/airflow.db" ]; then
    echo "▸ Initializing Airflow DB..."
    if [ "${AIRFLOW_MAJOR}" = "2" ]; then
        "${AIRFLOW_VENV}/bin/airflow" db init
        echo "▸ Creating Admin User (FAB auth)..."
        "${AIRFLOW_VENV}/bin/airflow" users create \
            --username admin \
            --firstname Admin \
            --lastname User \
            --role Admin \
            --email admin@example.com \
            --password admin
    else
        "${AIRFLOW_VENV}/bin/airflow" db migrate
    fi
    echo "  ✓ DB initialized"
fi

# Airflow 3: Always ensure SimpleAuthManager password file exists
if [ "${AIRFLOW_MAJOR}" != "2" ]; then
    echo '{"admin": "admin"}' > "${AIRFLOW_HOME}/simple_auth_manager_passwords.json.generated"
fi

# =============================================================================
# Step 3: Jupyter Server Proxy Config (version-specific)
# =============================================================================
mkdir -p /root/.jupyter

SERVICE_PREFIX="${JUPYTERHUB_SERVICE_PREFIX:-/}"

if [ "${AIRFLOW_MAJOR}" = "2" ]; then
    AIRFLOW_PROXY_NAME="airflow-webserver"
    AIRFLOW_PROXY_CMD="['${AIRFLOW_VENV}/bin/airflow', 'webserver', '--port', '{port}']"
    AIRFLOW_PROXY_ABSOLUTE="False"
    AIRFLOW_PROXY_TITLE="Airflow Webserver"
else
    AIRFLOW_PROXY_NAME="airflow-api"
    AIRFLOW_PROXY_CMD="['${AIRFLOW_VENV}/bin/airflow', 'api-server', '--port', '{port}']"
    AIRFLOW_PROXY_ABSOLUTE="True"
    AIRFLOW_PROXY_TITLE="Airflow API Server"
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
        'command': ['${AIRFLOW_VENV}/bin/python', '${AIRFLOW_HOME}/scheduler_wrapper.py'],
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

echo "  ✓ Proxy config: ${AIRFLOW_PROXY_NAME}"

# =============================================================================
# Step 4: Start Jupyter
# =============================================================================
echo ""
echo "▸ Starting JupyterLab (Airflow ${AIRFLOW_VERSION} / Python ${PYTHON_VERSION})..."
echo ""

if [ -n "${JUPYTERHUB_API_TOKEN}" ]; then
    exec "${JUPYTER_VENV}/bin/jupyterhub-singleuser" \
        --allow-root \
        --ip=0.0.0.0 \
        --port=8888 \
        --NotebookApp.token='' \
        --ServerApp.root_dir="${AIRFLOW_HOME}"
else
    exec "${JUPYTER_VENV}/bin/jupyter" lab \
        --ip=0.0.0.0 \
        --port=8888 \
        --no-browser \
        --allow-root \
        --NotebookApp.token=''
fi
