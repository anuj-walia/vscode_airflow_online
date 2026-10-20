#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# start.sh — workspace entrypoint (runs as uid 1000, under tini).
#
# Order matters:
#   1. materialise $AIRFLOW_HOME, which is an empty PVC on first spawn
#   2. derive the proxy-aware Airflow URLs from JUPYTERHUB_SERVICE_PREFIX
#   3. migrate the per-user SQLite metadata DB
#   4. provision Airflow UI credentials
#   5. exec jupyterhub-singleuser (or `jupyter lab` when run standalone)
#
# Everything this script writes lands on the user's PVC, so a pod restart is
# cheap and a PVC restore brings the whole workspace back.
# ---------------------------------------------------------------------------
set -euo pipefail

log() { printf '[workspace] %s\n' "$*"; }
fail() { printf '[workspace] ERROR: %s\n' "$*" >&2; exit 1; }

AIRFLOW_HOME="${AIRFLOW_HOME:-/opt/airflow}"
STATE_DIR="${AIRFLOW_HOME}/.airflow"
WORKSPACE_ASSETS=/srv/workspace

# ---------------------------------------------------------------------------
# 1. Workspace layout on the PVC
# ---------------------------------------------------------------------------
mkdir -p \
    "${AIRFLOW_HOME}/dags" \
    "${AIRFLOW_HOME}/logs" \
    "${AIRFLOW_HOME}/plugins" \
    "${AIRFLOW_HOME}/notebooks" \
    "${STATE_DIR}" \
    "${AIRFLOW_HOME}/.local/share/code-server" \
    "${AIRFLOW_HOME}/.config" \
    "${AIRFLOW_HOME}/.cache"

chmod 0700 "${STATE_DIR}"

# Keep editor/tool state on the PVC instead of a throwaway container layer.
export XDG_DATA_HOME="${AIRFLOW_HOME}/.local/share"
export XDG_CONFIG_HOME="${AIRFLOW_HOME}/.config"
export XDG_CACHE_HOME="${AIRFLOW_HOME}/.cache"
export HOME="${AIRFLOW_HOME}"

# Seed VS Code settings once; never overwrite what the user has since changed.
CODE_USER_DIR="${XDG_DATA_HOME}/code-server/User"
if [[ ! -f "${CODE_USER_DIR}/settings.json" ]]; then
    mkdir -p "${CODE_USER_DIR}"
    cp "${WORKSPACE_ASSETS}/vscode/settings.json" "${CODE_USER_DIR}/settings.json"
fi

# Terminals and debug sessions inherit the image's default DAGs folder.
# `workspace-dags <path>` records the user's choice in workspace.env; source it
# from .bashrc and .profile (once) so `airflow ...` in a terminal matches the services.
BASHRC_LINE='[ -f "${HOME}/.airflow/workspace.env" ] && . "${HOME}/.airflow/workspace.env"  # workspace-dags'
# .profile covers login shells (some terminals start `bash -l`), which do not read .bashrc.
for rc in "${HOME}/.bashrc" "${HOME}/.profile"; do
    if ! grep -qsF '# workspace-dags' "${rc}"; then
        printf '%s\n' "${BASHRC_LINE}" >> "${rc}"
    fi
done

# Seed the Airflow debug profiles into the workspace folder, once.
if [[ ! -f "${AIRFLOW_HOME}/.vscode/launch.json" ]]; then
    mkdir -p "${AIRFLOW_HOME}/.vscode"
    cp "${WORKSPACE_ASSETS}/vscode/launch.json" "${AIRFLOW_HOME}/.vscode/launch.json"
fi

# Seed the extensions built into the image. code-server resolves its extensions
# directory from XDG_DATA_HOME, which now points at the PVC, so without this the
# workspace would start with no Python extension. Copy only into an empty
# directory -- after that the user owns it and can install their own.
CODE_EXTENSIONS_DIR="${XDG_DATA_HOME}/code-server/extensions"
if [[ -d "${WORKSPACE_ASSETS}/vscode/extensions" ]] \
   && [[ -z "$(ls -A "${CODE_EXTENSIONS_DIR}" 2>/dev/null)" ]]; then
    log "seeding VS Code extensions"
    mkdir -p "${CODE_EXTENSIONS_DIR}"
    cp -r "${WORKSPACE_ASSETS}/vscode/extensions/." "${CODE_EXTENSIONS_DIR}/"
fi

# Seed example DAGs only into a genuinely empty dags folder.
if [[ "${WORKSPACE_SEED_EXAMPLES:-true}" == "true" ]] \
   && [[ -z "$(ls -A "${AIRFLOW_HOME}/dags" 2>/dev/null)" ]]; then
    log "seeding example DAGs"
    cp -r "${WORKSPACE_ASSETS}/examples/." "${AIRFLOW_HOME}/dags/"
fi

# ---------------------------------------------------------------------------
# 2. Proxy-aware Airflow URLs
#
# Under JupyterHub the workspace is served at /user/<name>/, and Airflow is
# proxied at /user/<name>/airflow-api/. Airflow's API server derives the
# FastAPI root_path from the *path* of [api] base_url, so every route --
# including the /execution mount that task workers call -- lives under that
# prefix. Two separate URLs are therefore needed:
#
#   base_url       what the browser uses, through the authenticated proxy
#   execution url  what scheduler/worker processes use; must NOT traverse the
#                  proxy (it would 403 on the JupyterHub auth layer and the
#                  resulting HTTPStatusError is unpicklable, killing the
#                  scheduler), so it hits 127.0.0.1:9091 directly -- with the
#                  same path prefix, or every call 404s.
# ---------------------------------------------------------------------------
SERVICE_PREFIX="${JUPYTERHUB_SERVICE_PREFIX:-/}"
AIRFLOW_PROXY_PATH="${SERVICE_PREFIX}airflow-api"

# WORKSPACE_AIRFLOW_BASE_URL lets an operator publish the real external origin
# (https://platform.example.com/user/<name>/airflow-api). The loopback default
# is the form that is known-good behind jupyter-server-proxy; only the path
# component affects routing.
export AIRFLOW__API__BASE_URL="${WORKSPACE_AIRFLOW_BASE_URL:-http://localhost:8888${AIRFLOW_PROXY_PATH}}"
export AIRFLOW__CORE__EXECUTION_API_SERVER_URL="http://127.0.0.1:9091${AIRFLOW_PROXY_PATH}/execution/"

log "AIRFLOW__API__BASE_URL=${AIRFLOW__API__BASE_URL}"
log "AIRFLOW__CORE__EXECUTION_API_SERVER_URL=${AIRFLOW__CORE__EXECUTION_API_SERVER_URL}"

# ---------------------------------------------------------------------------
# 3. Metadata database
#
# Per-user SQLite on the PVC. `db migrate` is idempotent and also handles the
# upgrade path when a workspace image with a newer Airflow is rolled out.
# ---------------------------------------------------------------------------
log "running airflow db migrate"
if ! airflow db migrate; then
    fail "airflow db migrate failed; inspect ${AIRFLOW_HOME}/airflow.db"
fi

# ---------------------------------------------------------------------------
# 4. Airflow UI credentials
#
# The pod is single-tenant and the API server is on loopback behind the Hub's
# OAuth, so there is no shared secret to leak -- but a static admin/admin is
# still not something to ship. Generate a random password once per workspace,
# store it 0600 on the PVC, and show it to the owner in the Services page.
#
# WORKSPACE_AIRFLOW_NO_LOGIN=true instead disables the Airflow login screen
# entirely (SimpleAuthManager treats every request as admin). Only set that
# where you are satisfied nothing but the pod's owner can reach 127.0.0.1:9091.
# ---------------------------------------------------------------------------
PASSWORD_FILE="${AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_PASSWORDS_FILE:-${STATE_DIR}/simple_auth_passwords.json}"
export AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_PASSWORDS_FILE="${PASSWORD_FILE}"

if [[ "${WORKSPACE_AIRFLOW_NO_LOGIN:-false}" == "true" ]]; then
    log "Airflow login disabled (simple_auth_manager_all_admins=True)"
    export AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_ALL_ADMINS=True
else
    export AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_ALL_ADMINS=False
    AIRFLOW_UI_USER="${WORKSPACE_AIRFLOW_USERNAME:-${JUPYTERHUB_USER:-admin}}"
    # SimpleAuthManager parses this as comma-separated user:role pairs, so a
    # username containing ',' or ':' would corrupt the list. OIDC subjects and
    # emails can contain neither, but normalise defensively.
    AIRFLOW_UI_USER="${AIRFLOW_UI_USER//[,:]/_}"
    export AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_USERS="${AIRFLOW_UI_USER}:admin"

    if [[ ! -s "${PASSWORD_FILE}" ]]; then
        log "generating Airflow UI password for '${AIRFLOW_UI_USER}'"
        AIRFLOW_UI_PASSWORD="$(python -c 'import secrets; print(secrets.token_urlsafe(18))')"
        umask 077
        python - "$PASSWORD_FILE" "$AIRFLOW_UI_USER" "$AIRFLOW_UI_PASSWORD" <<'PYGEN'
import json
import sys

path, user, password = sys.argv[1:4]
with open(path, "w", encoding="utf-8") as fh:
    json.dump({user: password}, fh)
PYGEN
        umask 022
        chmod 0600 "${PASSWORD_FILE}"
    fi
fi

# ---------------------------------------------------------------------------
# 5. Hand off to the Jupyter server
# ---------------------------------------------------------------------------
if [[ -n "${JUPYTERHUB_API_TOKEN:-}" ]]; then
    log "starting jupyterhub-singleuser"
    exec jupyterhub-singleuser \
        --ip=0.0.0.0 \
        --port="${WORKSPACE_PORT:-8888}" \
        --ServerApp.root_dir="${AIRFLOW_HOME}" \
        "$@"
fi

# Standalone / `docker run` path. No Hub in front, so keep the token on.
log "no JUPYTERHUB_API_TOKEN in environment; starting standalone JupyterLab"
exec jupyter lab \
    --ip=0.0.0.0 \
    --port="${WORKSPACE_PORT:-8888}" \
    --no-browser \
    --ServerApp.root_dir="${AIRFLOW_HOME}" \
    "$@"
