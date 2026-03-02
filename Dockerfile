# =============================================================================
# Single Unified Airflow + JupyterLab Docker Image
#
# This image does NOT include Apache Airflow at build time.
# Airflow is installed at first pod startup based on user-selected
# AIRFLOW_VERSION and PYTHON_VERSION env vars (set by JupyterHub).
# The PVC caches the installation so subsequent starts are instant.
#
# Build:  docker build -t airflow-jupyter:latest .
# =============================================================================

FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

# ---- Install Multiple Python Versions via deadsnakes PPA ----
RUN apt-get update && \
    apt-get install -y software-properties-common && \
    add-apt-repository -y ppa:deadsnakes/ppa && \
    apt-get update && apt-get install -y \
    python3.9 python3.9-venv python3.9-dev \
    python3.10 python3.10-venv python3.10-dev \
    python3.11 python3.11-venv python3.11-dev \
    python3.12 python3.12-venv python3.12-dev \
    git \
    curl \
    wget \
    build-essential \
    libsqlite3-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

# ---- Install Node.js (for code-server) ----
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs && \
    rm -rf /var/lib/apt/lists/*

# ---- Install Code Server (VS Code in browser) ----
RUN curl -fsSL https://code-server.dev/install.sh | sh

# ---- Jupyter Virtual Environment (uses Python 3.11 — stable for Jupyter) ----
RUN python3.11 -m venv /opt/jupyter_venv
RUN /opt/jupyter_venv/bin/pip install --no-cache-dir \
    jupyterlab \
    jupyterhub \
    jupyter-server-proxy

# ---- VS Code Extensions ----
RUN code-server --install-extension ms-python.python \
    && code-server --install-extension redhat.vscode-yaml \
    && code-server --install-extension janisdd.vscode-edit-csv

# ---- VS Code Default Python Interpreter ----
RUN mkdir -p /root/.local/share/code-server/User && \
    echo '{ "python.defaultInterpreterPath": "/opt/airflow/venv/bin/python" }' \
    > /root/.local/share/code-server/User/settings.json

# ---- Airflow Home (will be overlaid by PVC mount) ----
ENV AIRFLOW_HOME=/opt/airflow
RUN mkdir -p $AIRFLOW_HOME

# ---- Common env vars ----
ENV AIRFLOW__WEBSERVER__ENABLE_PROXY_FIX=True
ENV AIRFLOW__CORE__EXECUTOR=SequentialExecutor
ENV AIRFLOW__DATABASE__SQL_ALCHEMY_CONN=sqlite:////opt/airflow/airflow.db
ENV AIRFLOW__CORE__LOAD_EXAMPLES=False

# ---- Copy scripts to a path NOT hidden by PVC mount ----
COPY entrypoint.sh /opt/airflow-scripts/entrypoint.sh
COPY scheduler_wrapper.py /opt/airflow-scripts/scheduler_wrapper.py
RUN mkdir -p /opt/airflow-scripts/.vscode
COPY .vscode /opt/airflow-scripts/.vscode

RUN chmod +x /opt/airflow-scripts/entrypoint.sh

# ---- Default PATH: Jupyter venv first ----
ENV PATH="/opt/jupyter_venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

# ---- Working Directory ----
WORKDIR $AIRFLOW_HOME

# ---- Expose Ports ----
EXPOSE 8888 8080 9091 8999

# ---- Entrypoint ----
ENTRYPOINT ["/opt/airflow-scripts/entrypoint.sh"]
