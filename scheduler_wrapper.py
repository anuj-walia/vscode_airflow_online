import subprocess
import sys
import http.server
import socketserver
import threading
import os
import time
import urllib.parse
import signal
import glob
import html as html_module

# Configuration
SCHEDULER_LOG_FILE = "/opt/airflow/scheduler.log"
DAG_PROCESSOR_LOG_FILE = "/opt/airflow/dag_processor.log"
PORT = 8999
DEFAULT_DAGS_FOLDER = "/opt/airflow/dags"

# Global state
scheduler_process = None
scheduler_thread = None
dag_processor_process = None
dag_processor_thread = None
active_dags_folder = None

SETUP_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <title>Airflow Scheduler Setup</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
            color: #e0e0e0;
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
        }}
        .container {{
            background: rgba(255, 255, 255, 0.05);
            backdrop-filter: blur(10px);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 16px;
            padding: 48px;
            max-width: 600px;
            width: 90%;
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
        }}
        h1 {{
            font-size: 24px;
            font-weight: 600;
            margin-bottom: 8px;
            color: #fff;
        }}
        .subtitle {{
            color: #8899aa;
            margin-bottom: 32px;
            font-size: 14px;
        }}
        label {{
            display: block;
            font-size: 13px;
            font-weight: 500;
            color: #aabbcc;
            margin-bottom: 8px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        input[type="text"] {{
            width: 100%;
            padding: 12px 16px;
            background: rgba(0, 0, 0, 0.3);
            border: 1px solid rgba(255, 255, 255, 0.15);
            border-radius: 8px;
            color: #fff;
            font-size: 15px;
            font-family: 'SF Mono', 'Fira Code', monospace;
            outline: none;
            transition: border-color 0.2s;
        }}
        input[type="text"]:focus {{
            border-color: #017CEE;
        }}
        .hint {{
            font-size: 12px;
            color: #667788;
            margin-top: 8px;
            margin-bottom: 24px;
        }}
        button {{
            width: 100%;
            padding: 14px;
            background: linear-gradient(135deg, #017CEE, #0056b3);
            color: #fff;
            border: none;
            border-radius: 8px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            transition: transform 0.1s, box-shadow 0.2s;
        }}
        button:hover {{
            transform: translateY(-1px);
            box-shadow: 0 4px 16px rgba(1, 124, 238, 0.4);
        }}
        button:active {{
            transform: translateY(0);
        }}
        .error {{
            background: rgba(220, 53, 69, 0.15);
            border: 1px solid rgba(220, 53, 69, 0.3);
            color: #ff6b7a;
            padding: 12px 16px;
            border-radius: 8px;
            margin-bottom: 20px;
            font-size: 14px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>Airflow Scheduler</h1>
        <p class="subtitle">Configure and start the Airflow scheduler</p>
        {error}
        <form method="POST" action="">
            <label for="dags_folder">DAGs Folder Path</label>
            <input type="text" id="dags_folder" name="dags_folder" value="{default_folder}" autocomplete="off" />
            <p class="hint">Absolute path inside the container. The default folder is mounted from ./dags on your host.</p>
            <button type="submit">Start Scheduler</button>
        </form>
    </div>
</body>
</html>
"""

LOG_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <title>Airflow Scheduler Logs</title>
    <meta http-equiv="refresh" content="5">
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace;
            background-color: #1e1e1e;
            color: #d4d4d4;
            padding: 20px;
        }}
        .header {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 16px;
            padding-bottom: 16px;
            border-bottom: 1px solid #333;
        }}
        h1 {{
            font-size: 18px;
            font-weight: 600;
            color: #fff;
        }}
        .badge {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            background: rgba(0, 208, 132, 0.15);
            border: 1px solid rgba(0, 208, 132, 0.3);
            color: #00d084;
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: 500;
        }}
        .badge::before {{
            content: '';
            width: 6px;
            height: 6px;
            background: #00d084;
            border-radius: 50%;
            animation: pulse 2s infinite;
        }}
        @keyframes pulse {{
            0%, 100% {{ opacity: 1; }}
            50% {{ opacity: 0.4; }}
        }}
        .info {{
            color: #569cd6;
            font-size: 13px;
            margin-bottom: 16px;
        }}
        .stop-form {{
            display: inline;
        }}
        .stop-btn {{
            background: rgba(220, 53, 69, 0.15);
            border: 1px solid rgba(220, 53, 69, 0.3);
            color: #ff6b7a;
            padding: 4px 12px;
            border-radius: 6px;
            font-size: 12px;
            cursor: pointer;
            font-family: inherit;
        }}
        .stop-btn:hover {{
            background: rgba(220, 53, 69, 0.3);
        }}
        .section-header {{
            font-size: 14px;
            font-weight: 600;
            color: #569cd6;
            margin-top: 20px;
            margin-bottom: 8px;
            padding: 8px 12px;
            background: rgba(86, 156, 214, 0.1);
            border-left: 3px solid #569cd6;
            border-radius: 0 4px 4px 0;
        }}
        .section-header.dag-processor {{
            color: #00d084;
            background: rgba(0, 208, 132, 0.1);
            border-left-color: #00d084;
        }}
        pre {{
            white-space: pre-wrap;
            word-wrap: break-word;
            font-size: 13px;
            line-height: 1.5;
            max-height: 45vh;
            overflow-y: auto;
            padding: 12px;
            background: rgba(0, 0, 0, 0.3);
            border-radius: 6px;
            margin-bottom: 8px;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>Airflow Scheduler &amp; DAG Processor</h1>
        <div>
            <span class="badge">Running</span>
            <form class="stop-form" method="POST" action="stop" style="display:inline; margin-left: 8px;">
                <button class="stop-btn" type="submit">Stop &amp; Reconfigure</button>
            </form>
        </div>
    </div>
    <p class="info">DAGs folder: <strong>{dags_folder}</strong></p>
    <div class="section-header">Scheduler Logs</div>
    <pre>{scheduler_logs}</pre>
    <div class="section-header dag-processor">DAG Processor Logs</div>
    <pre>{dag_processor_logs}</pre>
</body>
</html>
"""


def run_scheduler(dags_folder):
    """Runs the Airflow scheduler with the specified DAGs folder."""
    global scheduler_process
    env = os.environ.copy()
    env["AIRFLOW__CORE__DAGS_FOLDER"] = dags_folder
    print(f"Starting scheduler with DAGs folder: {dags_folder}")

    with open(SCHEDULER_LOG_FILE, "w") as f:
        scheduler_process = subprocess.Popen(
            ["airflow", "scheduler"],
            stdout=f,
            stderr=subprocess.STDOUT,
            env=env
        )
        scheduler_process.wait()


def run_dag_processor(dags_folder):
    """Runs the Airflow DAG processor (required in Airflow 3.x)."""
    global dag_processor_process
    env = os.environ.copy()
    env["AIRFLOW__CORE__DAGS_FOLDER"] = dags_folder
    print(f"Starting dag-processor with DAGs folder: {dags_folder}")

    with open(DAG_PROCESSOR_LOG_FILE, "w") as f:
        dag_processor_process = subprocess.Popen(
            ["airflow", "dag-processor"],
            stdout=f,
            stderr=subprocess.STDOUT,
            env=env
        )
        dag_processor_process.wait()


def stop_scheduler():
    """Stops the running scheduler and dag-processor processes."""
    global scheduler_process, scheduler_thread, dag_processor_process, dag_processor_thread, active_dags_folder
    if scheduler_process and scheduler_process.poll() is None:
        scheduler_process.terminate()
        try:
            scheduler_process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            scheduler_process.kill()
    if dag_processor_process and dag_processor_process.poll() is None:
        dag_processor_process.terminate()
        try:
            dag_processor_process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            dag_processor_process.kill()
    scheduler_process = None
    scheduler_thread = None
    dag_processor_process = None
    dag_processor_thread = None
    active_dags_folder = None


class SchedulerHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Suppress default request logging to keep stdout clean
        pass

    def _get_base_url(self):
        """Get the base URL path, accounting for jupyter-server-proxy prefix."""
        # jupyter-server-proxy sets X-Forwarded-Prefix
        prefix = self.headers.get('X-Forwarded-Prefix', '')
        if prefix:
            return prefix.rstrip('/') + '/'
        return '/'

    def do_GET(self):
        # Accept both root and any proxy-prefixed paths
        path = self.path.split('?')[0].rstrip('/')
        if path == '' or path.endswith('/airflow-scheduler') or path == '/':
            if active_dags_folder and scheduler_thread and scheduler_thread.is_alive():
                self._serve_log_page()
            else:
                self._serve_setup_page()
        else:
            self.send_error(404)

    def do_POST(self):
        global scheduler_thread, active_dags_folder
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length).decode('utf-8')
        params = urllib.parse.parse_qs(post_data)
        base_url = self._get_base_url()
        path = self.path.split('?')[0].rstrip('/')

        if path.endswith('/stop'):
            stop_scheduler()
            self.send_response(303)
            self.send_header("Location", base_url)
            self.end_headers()
            return

        # Handle form submission
        dags_folder = params.get("dags_folder", [DEFAULT_DAGS_FOLDER])[0].strip()

        if not dags_folder:
            self._serve_setup_page(error="Please enter a DAGs folder path.")
            return

        if not os.path.isdir(dags_folder):
            self._serve_setup_page(
                error=f"Folder not found: {dags_folder}",
                prefill=dags_folder
            )
            return

        # Check if scheduler is already running
        if active_dags_folder and scheduler_thread and scheduler_thread.is_alive():
            self._serve_log_page()
            return

        # Start scheduler and dag-processor
        active_dags_folder = dags_folder
        scheduler_thread = threading.Thread(target=run_scheduler, args=(dags_folder,), daemon=True)
        scheduler_thread.start()
        dag_processor_thread = threading.Thread(target=run_dag_processor, args=(dags_folder,), daemon=True)
        dag_processor_thread.start()

        # Give it a moment to start, then show log page directly
        time.sleep(1)
        self._serve_log_page()

    def _serve_setup_page(self, error="", prefill=None):
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()

        error_html = ""
        if error:
            error_html = f'<div class="error">{html_module.escape(error)}</div>'

        page = SETUP_PAGE.format(
            error=error_html,
            default_folder=html_module.escape(prefill or DEFAULT_DAGS_FOLDER)
        )
        self.wfile.write(page.encode())

    def _serve_log_page(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()

        scheduler_logs = ""
        # First read startup logs from stdout capture
        try:
            with open(SCHEDULER_LOG_FILE, "r") as f:
                lines = f.readlines()
                scheduler_logs = "".join(lines[-20:])
        except FileNotFoundError:
            pass

        # Then read Airflow's native scheduler logs (where ongoing activity goes)
        try:
            log_dir = os.path.join(os.environ.get('AIRFLOW_HOME', '/opt/airflow'), 'logs', 'scheduler')
            log_files = sorted(
                glob.glob(os.path.join(log_dir, '**', '*.log'), recursive=True),
                key=os.path.getmtime,
                reverse=True
            )
            if log_files:
                with open(log_files[0], "r") as f:
                    lines = f.readlines()
                    scheduler_logs += "\n" + "".join(lines[-40:])
        except Exception:
            pass

        if not scheduler_logs.strip():
            scheduler_logs = "Waiting for scheduler logs...\n"
        scheduler_logs = html_module.escape(scheduler_logs)

        dag_processor_logs = ""
        try:
            with open(DAG_PROCESSOR_LOG_FILE, "r") as f:
                lines = f.readlines()
                dag_processor_logs = html_module.escape("".join(lines[-50:]))
        except FileNotFoundError:
            dag_processor_logs = "Waiting for dag-processor logs...\n"

        page = LOG_PAGE.format(
            dags_folder=html_module.escape(active_dags_folder or DEFAULT_DAGS_FOLDER),
            scheduler_logs=scheduler_logs,
            dag_processor_logs=dag_processor_logs
        )
        self.wfile.write(page.encode())


if __name__ == "__main__":
    with socketserver.TCPServer(("", PORT), SchedulerHandler) as httpd:
        print(f"Scheduler UI ready at port {PORT}")
        httpd.serve_forever()
