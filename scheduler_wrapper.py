import subprocess
import sys
import http.server
import socketserver
import threading
import os
import time

# Configuration
LOG_FILE = "/opt/airflow/scheduler.log"
PORT = 8999

def run_scheduler():
    """Runs the Airflow scheduler and pipes output to a log file."""
    with open(LOG_FILE, "w") as f:
        process = subprocess.Popen(
            ["airflow", "scheduler"],
            stdout=f,
            stderr=subprocess.STDOUT,
            env=os.environ.copy()
        )
        process.wait()

class LogHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            
            html = """
            <html>
            <head>
                <title>Airflow Scheduler Logs</title>
                <meta http-equiv="refresh" content="5">
                <style>
                    body { font-family: monospace; background-color: #1e1e1e; color: #d4d4d4; padding: 20px; }
                    pre { white-space: pre-wrap; word-wrap: break-word; }
                </style>
            </head>
            <body>
                <h1>Airflow Scheduler Logs</h1>
                <pre>
            """
            try:
                with open(LOG_FILE, "r") as f:
                    # Read last 100 lines
                    lines = f.readlines()
                    html += "".join(lines[-100:])
            except FileNotFoundError:
                html += "Waiting for logs..."
            
            html += """
                </pre>
            </body>
            </html>
            """
            self.wfile.write(html.encode())
        else:
            super().do_GET()

def start_server():
    """Starts a simple HTTP server to show logs."""
    with socketserver.TCPServer(("", PORT), LogHandler) as httpd:
        print(f"Serving logs at port {PORT}")
        httpd.serve_forever()

if __name__ == "__main__":
    # Start scheduler in a separate thread
    scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
    scheduler_thread.start()
    
    # Start web server to show logs
    start_server()
