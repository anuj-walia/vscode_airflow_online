# ---------------------------------------------------------------------------
# Airflow 3 developer workspace image
#
# Runs as a non-root user (uid/gid 1000) under jupyterhub-singleuser, and
# exposes three services through jupyter-server-proxy:
#
#   airflow-api        -> `airflow api-server` on 127.0.0.1:9091
#   airflow-scheduler  -> workspace_services.py supervisor on 127.0.0.1:8999
#   vscode             -> code-server on a proxy-assigned port
#
# NOTHING that the image needs at runtime may live under $AIRFLOW_HOME.
# In Kubernetes that path is the per-user PVC mount and shadows the image
# layer. Image content lives in /opt/venv and /srv/workspace; bin/start.sh
# populates $AIRFLOW_HOME on first boot.
# ---------------------------------------------------------------------------
ARG BASE_IMAGE=python:3.12-slim-bookworm
FROM ${BASE_IMAGE}

ARG AIRFLOW_VERSION=3.3.2
ARG PYTHON_VERSION=3.12
ARG CODE_SERVER_VERSION=4.138.0
ARG NB_UID=1000
ARG NB_GID=1000

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# --- OS packages ----------------------------------------------------------
# tini gives us a real PID 1 that reaps the subprocesses jupyter-server-proxy
# and the service supervisor fork. git/curl are developer tools users expect.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        git \
        less \
        libpq5 \
        openssh-client \
        procps \
        tini \
    && rm -rf /var/lib/apt/lists/*

# --- Non-root runtime user ------------------------------------------------
RUN groupadd --gid ${NB_GID} airflow \
    && useradd --uid ${NB_UID} --gid ${NB_GID} --create-home --shell /bin/bash airflow

# --- code-server ----------------------------------------------------------
# Pinned tarball rather than the install script, so the build is reproducible
# and does not depend on code-server.dev resolving "latest".
RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
        amd64) cs_arch=amd64 ;; \
        arm64) cs_arch=arm64 ;; \
        *) echo "unsupported architecture: $arch" >&2; exit 1 ;; \
    esac; \
    curl -fsSL -o /tmp/code-server.tar.gz \
        "https://github.com/coder/code-server/releases/download/v${CODE_SERVER_VERSION}/code-server-${CODE_SERVER_VERSION}-linux-${cs_arch}.tar.gz"; \
    mkdir -p /opt/code-server; \
    tar -xzf /tmp/code-server.tar.gz -C /opt/code-server --strip-components=1; \
    rm /tmp/code-server.tar.gz; \
    ln -s /opt/code-server/bin/code-server /usr/local/bin/code-server; \
    code-server --version

# --- Python environment ---------------------------------------------------
ENV VIRTUAL_ENV=/opt/venv
RUN python -m venv "${VIRTUAL_ENV}"
ENV PATH="${VIRTUAL_ENV}/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

COPY requirements.txt /tmp/requirements.txt

# Order matters. Jupyter goes in first, then Airflow *under its constraints*,
# so the constraint file is authoritative for every dependency the two share.
# Installing Airflow first and Jupyter unconstrained afterwards lets pip move a
# pinned transitive dependency out from under Airflow -- which is how you get a
# green build and a scheduler that fails at runtime.
#
# If the constrained install cannot satisfy a Jupyter pin it fails here, loudly,
# which is the correct place to find out.
RUN set -eux; \
    pip install --upgrade pip setuptools wheel; \
    pip install -r /tmp/requirements.txt; \
    pip install \
        "apache-airflow[postgres]==${AIRFLOW_VERSION}" \
        --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-${AIRFLOW_VERSION}/constraints-${PYTHON_VERSION}.txt"; \
    rm /tmp/requirements.txt; \
    pip check

# --- Workspace assets -----------------------------------------------------
# /srv/workspace is read-only image content. It must not be under AIRFLOW_HOME.
COPY --chown=root:root bin/ /srv/workspace/bin/
COPY --chown=root:root icons/ /srv/workspace/icons/
COPY --chown=root:root examples/ /srv/workspace/examples/
COPY --chown=root:root vscode/launch.json /srv/workspace/vscode/launch.json
COPY --chown=root:root jupyter_server_config.py ${VIRTUAL_ENV}/etc/jupyter/jupyter_server_config.py
RUN chmod 0755 /srv/workspace/bin/*.sh /srv/workspace/bin/*.py /srv/workspace/bin/workspace-dags \
    && ln -s /srv/workspace/bin/workspace-dags /usr/local/bin/workspace-dags

# VS Code settings and extensions are image content. code-server resolves its
# user-data and extensions directories from XDG_DATA_HOME, which bin/start.sh
# points at the PVC -- so start.sh seeds both from here on first boot. Users can
# then install their own extensions, and those persist with the rest of their
# workspace.
RUN mkdir -p /srv/workspace/vscode \
    && printf '%s\n' '{' \
        '  "python.defaultInterpreterPath": "/opt/venv/bin/python",' \
        '  "python.terminal.activateEnvironment": true,' \
        '  "files.exclude": { "**/__pycache__": true },' \
        '  "telemetry.telemetryLevel": "off"' \
        '}' > /srv/workspace/vscode/settings.json

ENV WORKSPACE_EXTENSIONS_DIR=/srv/workspace/vscode/extensions
RUN set -eux; \
    for extension in ms-python.python redhat.vscode-yaml janisdd.vscode-edit-csv; do \
        code-server --extensions-dir "${WORKSPACE_EXTENSIONS_DIR}" \
                    --install-extension "$extension"; \
    done; \
    ls "${WORKSPACE_EXTENSIONS_DIR}"

# code-server creates its extensions tree 0700 as root. Everything under
# /srv/workspace has to be readable by uid 1000, which is the only user that
# ever reads it -- start.sh copies from here onto the user's volume.
RUN chmod -R a+rX /srv/workspace

# --- Airflow defaults -----------------------------------------------------
# Only settings that are correct for every deployment. Anything environment
# specific is injected by the Helm chart as AIRFLOW__SECTION__KEY env vars.
ENV AIRFLOW_HOME=/opt/airflow \
    AIRFLOW__CORE__EXECUTOR=LocalExecutor \
    AIRFLOW__CORE__LOAD_EXAMPLES=False \
    AIRFLOW__CORE__DAGS_FOLDER=/opt/airflow/dags \
    AIRFLOW__DATABASE__SQL_ALCHEMY_CONN=sqlite:////opt/airflow/airflow.db \
    AIRFLOW__LOGGING__BASE_LOG_FOLDER=/opt/airflow/logs \
    AIRFLOW__CORE__PLUGINS_FOLDER=/opt/airflow/plugins \
    AIRFLOW__API__HOST=127.0.0.1 \
    AIRFLOW__API__PORT=9091 \
    AIRFLOW__CORE__AUTH_MANAGER=airflow.api_fastapi.auth.managers.simple.simple_auth_manager.SimpleAuthManager \
    AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_PASSWORDS_FILE=/opt/airflow/.airflow/simple_auth_passwords.json

# $AIRFLOW_HOME is a mount point in Kubernetes; create it owned by the runtime
# user so the PVC's fsGroup lines up and a bare `docker run` also works.
RUN mkdir -p ${AIRFLOW_HOME} && chown ${NB_UID}:${NB_GID} ${AIRFLOW_HOME}

USER ${NB_UID}:${NB_GID}
WORKDIR ${AIRFLOW_HOME}

# JupyterLab only. The Airflow API server and the supervisor bind to loopback
# and are reachable solely through the authenticated jupyter-server-proxy.
EXPOSE 8888

ENTRYPOINT ["/usr/bin/tini", "-g", "--"]
CMD ["/srv/workspace/bin/start.sh"]
