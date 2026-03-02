# =============================================================================
# Unified Airflow + JupyterLab Dockerfile
#
# Supports both Airflow 2.x and 3.x via build args.
# Usage:
#   docker build --build-arg AIRFLOW_VERSION=2.11.0 --build-arg PYTHON_VERSION=3.11 \
#                -t airflow-jupyter:2.11.0-py3.11 .
# =============================================================================

ARG PYTHON_VERSION=3.11
FROM python:${PYTHON_VERSION}-slim

# Re-declare ARGs after FROM (they're reset)
ARG AIRFLOW_VERSION=2.11.0
ARG PYTHON_VERSION=3.11

# Persist versions as env vars for runtime detection
ENV AIRFLOW_VERSION=${AIRFLOW_VERSION}
ENV PYTHON_VERSION=${PYTHON_VERSION}

# ---- System Dependencies (including git for repo integration) ----
RUN apt-get update && apt-get install -y \
    git \
    curl \
    nodejs \
    npm \
    build-essential \
    libsqlite3-dev \
    && rm -rf /var/lib/apt/lists/*

# ---- Code Server (VS Code in browser) ----
RUN curl -fsSL https://code-server.dev/install.sh | sh

# ---- Python Virtual Environment ----
RUN python -m venv /opt/airflow_venv

# ---- Jupyter Packages ----
COPY requirements.txt /tmp/requirements.txt
RUN /opt/airflow_venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt

# ---- Install Apache Airflow (version-specific with constraints) ----
RUN /opt/airflow_venv/bin/pip install --no-cache-dir \
    "apache-airflow==${AIRFLOW_VERSION}" \
    --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-${AIRFLOW_VERSION}/constraints-${PYTHON_VERSION}.txt"

# ---- Airflow Home ----
ENV AIRFLOW_HOME=/opt/airflow
RUN mkdir -p $AIRFLOW_HOME

# ---- Common Airflow env vars ----
ENV AIRFLOW__WEBSERVER__ENABLE_PROXY_FIX=True
ENV AIRFLOW__CORE__EXECUTOR=SequentialExecutor
ENV AIRFLOW__DATABASE__SQL_ALCHEMY_CONN=sqlite:////opt/airflow/airflow.db
ENV AIRFLOW__CORE__LOAD_EXAMPLES=False

# ---- SVG Icons for Jupyter Launcher ----
RUN mkdir -p /opt/airflow/icons && \
    echo '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><circle cx="32" cy="32" r="30" fill="#017CEE"/><text x="32" y="42" text-anchor="middle" font-size="28" font-family="Arial" fill="white" font-weight="bold">A</text></svg>' > /opt/airflow/icons/airflow.svg && \
    echo '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><circle cx="32" cy="32" r="30" fill="#4A4A4A"/><text x="32" y="42" text-anchor="middle" font-size="24" font-family="Arial" fill="#00D084" font-weight="bold">S</text></svg>' > /opt/airflow/icons/airflow-scheduler.svg && \
    echo '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="12" fill="#0078D7"/><text x="32" y="44" text-anchor="middle" font-size="26" font-family="Arial" fill="white" font-weight="bold">VS</text></svg>' > /opt/airflow/icons/vscode.svg

# ---- VS Code Extensions ----
RUN code-server --install-extension ms-python.python \
    && code-server --install-extension redhat.vscode-yaml \
    && code-server --install-extension janisdd.vscode-edit-csv

# ---- VS Code Default Python Interpreter ----
RUN mkdir -p /root/.local/share/code-server/User && \
    echo '{ "python.defaultInterpreterPath": "/opt/airflow_venv/bin/python" }' > /root/.local/share/code-server/User/settings.json

# ---- Copy scripts to a path NOT hidden by PVC mount ----
COPY entrypoint.sh /opt/airflow-scripts/entrypoint.sh
COPY scheduler_wrapper.py /opt/airflow-scripts/scheduler_wrapper.py
RUN mkdir -p /opt/airflow/.vscode
COPY .vscode /opt/airflow-scripts/.vscode

RUN chmod +x /opt/airflow-scripts/entrypoint.sh

# ---- Working Directory ----
WORKDIR $AIRFLOW_HOME

# ---- Expose Ports ----
EXPOSE 8888 8080 9091 8999

# ---- Default PATH includes venv ----
ENV PATH="/opt/airflow_venv/bin:$PATH"

# ---- Entrypoint ----
ENTRYPOINT ["/opt/airflow-scripts/entrypoint.sh"]
