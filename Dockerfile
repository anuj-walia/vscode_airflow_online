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

# Airflow 3.x Configuration for Proxy
ENV AIRFLOW__WEBSERVER__BASE_URL=http://localhost:8888/airflow-webserver
ENV AIRFLOW__WEBSERVER__ENABLE_PROXY_FIX=True
ENV AIRFLOW__API__BASE_URL=http://localhost:8888/airflow-api
ENV AIRFLOW__CORE__EXECUTOR=SequentialExecutor
ENV AIRFLOW__DATABASE__SQL_ALCHEMY_CONN=sqlite:////opt/airflow/airflow.db
ENV AIRFLOW__CORE__LOAD_EXAMPLES=False

# Configure Jupyter Server Proxy for Airflow Webserver, API Server, and Scheduler
# We append to the jupyter_server_config.py if it exists, or create it
RUN mkdir -p /root/.jupyter && \
    echo "c.ServerProxy.servers = { \
    'airflow-webserver': { \
    'command': ['/opt/airflow_venv/bin/airflow', 'webserver', '--port', '{port}'], \
    'timeout': 120, \
    'absolute_url': True, \
    'launcher_entry': { \
    'title': 'Airflow Webserver', \
    'icon_path': '/opt/airflow_venv/lib/python3.11/site-packages/airflow/www/static/pin_100.png' \
    } \
    }, \
    'airflow-api': { \
    'command': ['/opt/airflow_venv/bin/airflow', 'api', '--port', '{port}'], \
    'timeout': 120, \
    'absolute_url': True, \
    'launcher_entry': { \
    'title': 'Airflow API Server', \
    'icon_path': '/opt/airflow_venv/lib/python3.11/site-packages/airflow/www/static/pin_100.png' \
    } \
    }, \
    'airflow-scheduler': { \
    'command': ['/opt/airflow_venv/bin/python', '/opt/airflow/scheduler_wrapper.py'], \
    'absolute_url': False, \
    'port': 8999, \
    'timeout': 120, \
    'launcher_entry': { \
    'title': 'Airflow Scheduler Logs', \
    'icon_path': '' \
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
EXPOSE 8888 8080 9091 8999

# Set default shell to use the venv
ENV PATH="/opt/airflow_venv/bin:$PATH"

# Entrypoint
ENTRYPOINT ["/opt/airflow/entrypoint.sh"]
