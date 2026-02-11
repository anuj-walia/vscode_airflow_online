FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    git \
    curl \
    nodejs \
    npm \
    build-essential \
    libsqlite3-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Code Server (VS Code)
RUN curl -fsSL https://code-server.dev/install.sh | sh

# Create virtual environment for Airflow
RUN python -m venv /opt/airflow_venv

# Copy requirements
COPY requirements.txt /tmp/requirements.txt

# Install Python dependencies in the virtual environment
RUN /opt/airflow_venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt

# Install Jupyter and proxies in the base environment (or venv, but user asked for venv to be default)
# We'll install jupyter in the venv as well so it has access to airflow libs
RUN /opt/airflow_venv/bin/pip install --no-cache-dir \
    jupyterlab \
    jupyterhub \
    jupyter-server-proxy \
    jupyter-vscode-proxy

# Set up Airflow Home
ENV AIRFLOW_HOME=/opt/airflow
RUN mkdir -p $AIRFLOW_HOME

# Airflow Configuration for Proxy
ENV AIRFLOW__WEBSERVER__BASE_URL=http://localhost:8888/airflow-webserver
ENV AIRFLOW__WEBSERVER__ENABLE_PROXY_FIX=True
ENV AIRFLOW__CORE__EXECUTOR=SequentialExecutor
ENV AIRFLOW__DATABASE__SQL_ALCHEMY_CONN=sqlite:////opt/airflow/airflow.db
ENV AIRFLOW__CORE__LOAD_EXAMPLES=False

# Create SVG icons for Jupyter launcher (icon_path requires SVG, not PNG)
RUN mkdir -p /opt/airflow/icons && \
    echo '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><circle cx="32" cy="32" r="30" fill="#017CEE"/><text x="32" y="42" text-anchor="middle" font-size="28" font-family="Arial" fill="white" font-weight="bold">A</text></svg>' > /opt/airflow/icons/airflow-webserver.svg && \
    echo '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><circle cx="32" cy="32" r="30" fill="#4A4A4A"/><text x="32" y="42" text-anchor="middle" font-size="24" font-family="Arial" fill="#00D084" font-weight="bold">S</text></svg>' > /opt/airflow/icons/airflow-scheduler.svg

# Configure Jupyter Server Proxy for Airflow Webserver and Scheduler
RUN mkdir -p /root/.jupyter && \
    echo "c.ServerProxy.servers = { \
    'airflow-webserver': { \
    'command': ['/opt/airflow_venv/bin/airflow', 'webserver', '--port', '{port}'], \
    'timeout': 120, \
    'absolute_url': True, \
    'launcher_entry': { \
    'title': 'Airflow Webserver', \
    'icon_path': '/opt/airflow/icons/airflow-webserver.svg' \
    } \
    }, \
    'airflow-scheduler': { \
    'command': ['/opt/airflow_venv/bin/python', '/opt/airflow/scheduler_wrapper.py'], \
    'absolute_url': False, \
    'port': 8999, \
    'timeout': 120, \
    'launcher_entry': { \
    'title': 'Airflow Scheduler Logs', \
    'icon_path': '/opt/airflow/icons/airflow-scheduler.svg' \
    } \
    } \
    }" >> /root/.jupyter/jupyter_server_config.py

# Install VS Code Extensions
RUN code-server --install-extension ms-python.python \
    && code-server --install-extension redhat.vscode-yaml \
    && code-server --install-extension janisdd.vscode-edit-csv

# Configure VS Code Default Python Interpreter
RUN mkdir -p /root/.local/share/code-server/User && \
    echo '{ "python.defaultInterpreterPath": "/opt/airflow_venv/bin/python" }' > /root/.local/share/code-server/User/settings.json

# Copy scripts
COPY entrypoint.sh /opt/airflow/entrypoint.sh
COPY scheduler_wrapper.py /opt/airflow/scheduler_wrapper.py
COPY .vscode /opt/airflow/.vscode

# Make scripts executable
RUN chmod +x /opt/airflow/entrypoint.sh

# Set working directory
WORKDIR $AIRFLOW_HOME

# Expose ports
EXPOSE 8888 8080 8999

# Set default shell to use the venv
ENV PATH="/opt/airflow_venv/bin:$PATH"

# Entrypoint
ENTRYPOINT ["/opt/airflow/entrypoint.sh"]
