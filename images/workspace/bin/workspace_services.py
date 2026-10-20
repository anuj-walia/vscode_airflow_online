#!/usr/bin/env python
"""
workspace_services.py — supervisor and log viewer for the Airflow 3 services
that run inside a developer workspace pod.

Replaces the fire-and-forget threaded launcher used by the single-container
branches. Differences that matter in production:

  * services are declared, not entered into a form, so a restarted pod comes
    back in the same state without the user touching anything;
  * each child is restarted with exponential backoff when it exits non-zero,
    and given up on after a bounded number of attempts rather than silently
    disappearing;
  * SIGTERM is propagated and awaited, so `kubectl delete pod` and the idle
    culler shut Airflow down cleanly instead of SIGKILLing mid-task;
  * logs are size-capped on the PVC;
  * /healthz reports per-service state for probes and debugging.

Bound to loopback: jupyter-server-proxy is the only way in, which means the
JupyterHub OAuth session for this user is the only way in.
"""

from __future__ import annotations

import html
import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

AIRFLOW_HOME = os.environ.get("AIRFLOW_HOME", "/opt/airflow")
LOG_DIR = os.path.join(AIRFLOW_HOME, "logs", "workspace")
PORT = int(os.environ.get("WORKSPACE_SERVICES_PORT", "8999"))
BIND = os.environ.get("WORKSPACE_SERVICES_BIND", "127.0.0.1")
AIRFLOW_BIN = os.environ.get("AIRFLOW_BIN", "/opt/venv/bin/airflow")

# Roll a service log once it passes this size, keeping one previous file.
MAX_LOG_BYTES = int(os.environ.get("WORKSPACE_LOG_MAX_BYTES", str(16 * 1024 * 1024)))
TAIL_LINES = 400

# Restart policy for a child that exits on its own.
MAX_RESTARTS = int(os.environ.get("WORKSPACE_MAX_RESTARTS", "5"))
BACKOFF_BASE_SECONDS = 2
BACKOFF_CAP_SECONDS = 60

# Services are started automatically unless the operator opts out. Set to a
# comma-separated subset (or "") to change what runs.
DEFAULT_AUTOSTART = "scheduler,dag-processor,triggerer"


@dataclass
class Service:
    name: str
    argv: list[str]
    description: str
    process: subprocess.Popen | None = None
    thread: threading.Thread | None = None
    restarts: int = 0
    last_exit: int | None = None
    started_at: float | None = None
    give_up: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def log_path(self) -> str:
        return os.path.join(LOG_DIR, f"{self.name}.log")

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    @property
    def state(self) -> str:
        if self.running:
            return "running"
        if self.give_up:
            return "failed"
        return "stopped"


# `airflow dag-processor` is a separate component in Airflow 3; without it the
# scheduler never sees a DAG file change. `triggerer` is what makes deferrable
# operators work, which is most of the standard provider's sensors.
SERVICES: dict[str, Service] = {
    s.name: s
    for s in [
        Service(
            name="scheduler",
            argv=[AIRFLOW_BIN, "scheduler"],
            description="Schedules DAG runs and queues task instances.",
        ),
        Service(
            name="dag-processor",
            argv=[AIRFLOW_BIN, "dag-processor"],
            description="Parses the DAGs folder and serialises DAGs into the metadata DB.",
        ),
        Service(
            name="triggerer",
            argv=[AIRFLOW_BIN, "triggerer"],
            description="Runs the async event loop that deferrable operators wait on.",
        ),
    ]
}

_shutting_down = threading.Event()


def _roll_log(path: str) -> None:
    try:
        if os.path.exists(path) and os.path.getsize(path) > MAX_LOG_BYTES:
            os.replace(path, path + ".1")
    except OSError:
        pass


def _supervise(service: Service) -> None:
    """Run `service` and restart it with backoff until told to stop."""
    while not _shutting_down.is_set():
        _roll_log(service.log_path)
        try:
            log_file = open(service.log_path, "a", buffering=1, encoding="utf-8")
        except OSError as exc:
            print(f"[services] cannot open {service.log_path}: {exc}", file=sys.stderr)
            service.give_up = True
            return

        with log_file:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            log_file.write(f"\n===== {service.name} starting at {stamp} =====\n")
            try:
                proc = subprocess.Popen(
                    service.argv,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    env=os.environ.copy(),
                    cwd=AIRFLOW_HOME,
                    start_new_session=True,
                )
            except OSError as exc:
                log_file.write(f"failed to start: {exc}\n")
                service.give_up = True
                return

            with service._lock:
                service.process = proc
                service.started_at = time.time()

            exit_code = proc.wait()
            service.last_exit = exit_code
            log_file.write(f"===== {service.name} exited with {exit_code} =====\n")

        if _shutting_down.is_set() or exit_code == 0:
            return

        service.restarts += 1
        if service.restarts > MAX_RESTARTS:
            service.give_up = True
            print(
                f"[services] {service.name} failed {service.restarts} times; giving up",
                file=sys.stderr,
            )
            return

        delay = min(BACKOFF_BASE_SECONDS**service.restarts, BACKOFF_CAP_SECONDS)
        print(
            f"[services] {service.name} exited {exit_code}; "
            f"restart {service.restarts}/{MAX_RESTARTS} in {delay}s",
            file=sys.stderr,
        )
        if _shutting_down.wait(delay):
            return


def start(service: Service) -> None:
    with service._lock:
        if service.running or (service.thread and service.thread.is_alive()):
            return
        service.restarts = 0
        service.give_up = False
        service.thread = threading.Thread(
            target=_supervise, args=(service,), daemon=True, name=f"supervise-{service.name}"
        )
        service.thread.start()


def stop(service: Service, timeout: float = 30.0) -> None:
    proc = service.process
    if proc is None or proc.poll() is not None:
        service.process = None
        return
    # start_new_session=True put the child in its own process group, so this
    # reaches the Airflow subprocesses it forked too.
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        proc.terminate()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        print(f"[services] {service.name} ignored SIGTERM; killing", file=sys.stderr)
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        proc.wait(timeout=10)
    service.process = None


def shutdown(*_args) -> None:
    if _shutting_down.is_set():
        return
    _shutting_down.set()
    print("[services] shutting down Airflow services", file=sys.stderr)
    for service in SERVICES.values():
        stop(service)


def tail(path: str, lines: int = TAIL_LINES) -> str:
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            # 2 KiB per line is a generous upper bound for Airflow log lines.
            block = min(size, lines * 2048)
            fh.seek(size - block)
            data = fh.read().decode("utf-8", errors="replace")
        return "\n".join(data.splitlines()[-lines:])
    except FileNotFoundError:
        return "(no output yet)"
    except OSError as exc:
        return f"(cannot read log: {exc})"


def airflow_credentials() -> tuple[str, str] | None:
    """Return (username, password) for the Airflow UI, if one was generated."""
    if os.environ.get("AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_ALL_ADMINS") == "True":
        return None
    path = os.environ.get("AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_PASSWORDS_FILE", "")
    try:
        with open(path, encoding="utf-8") as fh:
            users = json.load(fh)
        username, password = next(iter(users.items()))
        return username, password
    except (OSError, ValueError, StopIteration):
        return None


PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Airflow Services</title>
<meta http-equiv="refresh" content="10">
<style>
  :root {{ color-scheme: dark; }}
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
         background:#12141a; color:#dfe3ea; padding:28px; line-height:1.5; }}
  h1 {{ font-size:20px; font-weight:600; color:#fff; }}
  .sub {{ color:#7d8799; font-size:13px; margin-bottom:24px; }}
  .grid {{ display:grid; gap:14px; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); }}
  .card {{ background:#1a1d26; border:1px solid #262b38; border-radius:10px; padding:16px; }}
  .card h2 {{ font-size:14px; font-weight:600; color:#fff; display:flex;
              align-items:center; justify-content:space-between; }}
  .card p {{ font-size:12px; color:#7d8799; margin:6px 0 12px; }}
  .pill {{ font-size:11px; font-weight:600; padding:3px 10px; border-radius:20px; }}
  .running {{ background:rgba(0,208,132,.15); color:#00d084; border:1px solid rgba(0,208,132,.3); }}
  .stopped {{ background:rgba(125,135,153,.15); color:#9aa3b2; border:1px solid rgba(125,135,153,.3); }}
  .failed  {{ background:rgba(220,53,69,.15); color:#ff6b7a; border:1px solid rgba(220,53,69,.3); }}
  .meta {{ font-size:11px; color:#5d6676; font-family:ui-monospace,'SF Mono',monospace; }}
  form {{ display:inline; }}
  button {{ background:#222836; border:1px solid #333b4d; color:#c8cfdb; padding:5px 12px;
            border-radius:6px; font-size:12px; cursor:pointer; margin-right:6px; }}
  button:hover {{ background:#2c3444; color:#fff; }}
  .creds {{ background:#1a1d26; border:1px solid #262b38; border-radius:10px;
            padding:16px; margin:22px 0; font-size:13px; }}
  .creds code {{ background:#0e1016; padding:2px 8px; border-radius:4px;
                 font-family:ui-monospace,'SF Mono',monospace; color:#7ec9ff; }}
  h3 {{ font-size:13px; font-weight:600; color:#fff; margin:26px 0 8px; }}
  pre {{ background:#0e1016; border:1px solid #1f2430; border-radius:8px; padding:14px;
         font-family:ui-monospace,'SF Mono',monospace; font-size:12px; line-height:1.45;
         max-height:460px; overflow:auto; white-space:pre-wrap; word-wrap:break-word; }}
  .tabs {{ margin:26px 0 0; }}
  .tabs a {{ color:#9aa3b2; text-decoration:none; font-size:12px; padding:5px 12px;
             border:1px solid #262b38; border-radius:6px; margin-right:6px; display:inline-block; }}
  .tabs a.on {{ background:#222836; color:#fff; border-color:#3a4356; }}
</style>
</head>
<body>
  <h1>Airflow Services</h1>
  <p class="sub">DAGs folder: <strong>{dags_folder}</strong> &middot; auto-refreshes every 10s</p>
  {credentials}
  <div class="grid">{cards}</div>
  <h3>Logs</h3>
  <div class="tabs">{tabs}</div>
  <pre>{logs}</pre>
</body>
</html>
"""

CARD = """<div class="card">
  <h2>{name} <span class="pill {state}">{state}</span></h2>
  <p>{description}</p>
  <div class="meta">{meta}</div>
  <div style="margin-top:12px">
    <form method="post" action="{action_prefix}start"><input type="hidden" name="service" value="{name}"><button {start_disabled}>Start</button></form>
    <form method="post" action="{action_prefix}stop"><input type="hidden" name="service" value="{name}"><button {stop_disabled}>Stop</button></form>
    <form method="post" action="{action_prefix}restart"><input type="hidden" name="service" value="{name}"><button>Restart</button></form>
  </div>
</div>"""

CREDS = """<div class="creds">
  Airflow UI sign-in for this workspace &mdash; username <code>{user}</code>,
  password <code>{password}</code>.
  Generated once per workspace and stored at <code>{path}</code> (mode 0600, on your volume).
</div>"""

NO_CREDS = """<div class="creds">
  The Airflow login screen is disabled for this workspace
  (<code>simple_auth_manager_all_admins=True</code>); the API server is reachable
  only over loopback, through your authenticated JupyterHub session.
</div>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "AirflowWorkspaceServices/1.0"

    def log_message(self, fmt, *args):  # keep the pod log readable
        pass

    # jupyter-server-proxy mounts this app under /user/<name>/airflow-scheduler/.
    # Forms post to paths relative to that prefix, so build them from the
    # request path rather than assuming a root mount.
    def _action_prefix(self) -> str:
        path = urllib.parse.urlparse(self.path).path
        if not path.endswith("/"):
            path += "/"
        return path

    def _selected(self) -> str:
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        name = qs.get("log", [""])[0]
        return name if name in SERVICES else "scheduler"

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path.rstrip("/")
        if path.endswith("/healthz"):
            return self._send_json(
                HTTPStatus.OK,
                {
                    name: {
                        "state": svc.state,
                        "restarts": svc.restarts,
                        "last_exit": svc.last_exit,
                        "uptime_seconds": (
                            round(time.time() - svc.started_at) if svc.started_at and svc.running else 0
                        ),
                    }
                    for name, svc in SERVICES.items()
                },
            )
        return self._send_page()

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path.rstrip("/")
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        name = urllib.parse.parse_qs(body).get("service", [""])[0]
        service = SERVICES.get(name)

        if service is not None:
            if path.endswith("/start"):
                start(service)
            elif path.endswith("/stop"):
                stop(service)
            elif path.endswith("/restart"):
                stop(service)
                service.thread = None
                start(service)

        # Redirect back to the page the form was posted from, one level up.
        target = self._action_prefix().rsplit("/", 2)[0] + "/"
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", target)
        self.end_headers()

    def _send_json(self, status, payload):
        data = json.dumps(payload, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_page(self):
        prefix = self._action_prefix()
        selected = self._selected()

        cards = []
        for name, svc in SERVICES.items():
            if svc.running:
                meta = f"pid {svc.process.pid} &middot; up {round(time.time() - (svc.started_at or 0))}s"
            elif svc.give_up:
                meta = f"gave up after {svc.restarts} restarts &middot; last exit {svc.last_exit}"
            else:
                meta = "not running" if svc.last_exit is None else f"last exit {svc.last_exit}"
            cards.append(
                CARD.format(
                    name=html.escape(name),
                    state=svc.state,
                    description=html.escape(svc.description),
                    meta=meta,
                    action_prefix=html.escape(prefix),
                    start_disabled="disabled" if svc.running else "",
                    stop_disabled="" if svc.running else "disabled",
                )
            )

        tabs = "".join(
            f'<a class="{"on" if n == selected else ""}" href="?log={n}">{html.escape(n)}</a>'
            for n in SERVICES
        )

        creds = airflow_credentials()
        if creds is None:
            credentials_html = NO_CREDS
        else:
            user, password = creds
            credentials_html = CREDS.format(
                user=html.escape(user),
                password=html.escape(password),
                path=html.escape(
                    os.environ.get("AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_PASSWORDS_FILE", "")
                ),
            )

        page = PAGE.format(
            dags_folder=html.escape(
                os.environ.get("AIRFLOW__CORE__DAGS_FOLDER", os.path.join(AIRFLOW_HOME, "dags"))
            ),
            credentials=credentials_html,
            cards="".join(cards),
            tabs=tabs,
            logs=html.escape(tail(SERVICES[selected].log_path)),
        )
        data = page.encode()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    os.makedirs(LOG_DIR, exist_ok=True)
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    autostart = os.environ.get("WORKSPACE_AUTOSTART_SERVICES", DEFAULT_AUTOSTART)
    for name in (n.strip() for n in autostart.split(",") if n.strip()):
        if name in SERVICES:
            print(f"[services] auto-starting {name}", file=sys.stderr)
            start(SERVICES[name])
        else:
            print(f"[services] unknown service in autostart: {name}", file=sys.stderr)

    server = ThreadingHTTPServer((BIND, PORT), Handler)
    server.daemon_threads = True
    print(f"[services] listening on {BIND}:{PORT}", file=sys.stderr)
    try:
        server.serve_forever()
    finally:
        shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
