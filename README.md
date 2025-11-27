# Docker Project: Airflow + Jupyter + VS Code

This project provides a complete Docker-based development environment for Apache Airflow, integrated with JupyterLab and VS Code (code-server).

## Features

-   **Apache Airflow 2.11.0**:
    -   Configured with `SequentialExecutor` and SQLite.
    -   Webserver and Scheduler logs accessible via Jupyter Launcher.
    -   Virtual environment at `/opt/airflow_venv`.
-   **JupyterLab**:
    -   Serves as the main entry point.
    -   Includes `jupyter-server-proxy` for accessing Airflow and VS Code.
-   **VS Code (code-server)**:
    -   Integrated into JupyterLab.
    -   Pre-installed extensions: Python, YAML, CSV Editor.
    -   Pre-configured debugging for Airflow DAGs and tasks.
    -   Git installed for version control.
-   **Python 3.11**: Base image.

## Quick Start

### Prerequisites
-   Docker and Docker Compose installed.

### Running the Project

1.  **Start the container**:
    ```bash
    docker compose up --build
    ```

2.  **Access the Interface**:
    -   Open your browser and go to `http://localhost:8888`.
    -   You will see the JupyterLab interface.

3.  **Launch Tools**:
    -   **VS Code**: Click the "VS Code" icon in the launcher.
    -   **Airflow Webserver**: Click the "Airflow Webserver" icon.
    -   **Scheduler Logs**: Click the "Airflow Scheduler Logs" icon.

## Development

### VS Code Debugging
The project includes a pre-configured `.vscode/launch.json` (baked into the image) with the following configurations:

1.  **Python: Current File**: Runs the currently open Python file.
2.  **Airflow: Test Task**: Debugs a specific task (prompts for DAG ID, Task ID, Date).
3.  **Airflow: Test DAG (CLI)**: Debugs a full DAG run (prompts for DAG ID, Date).

### Source Control
The project is initialized as a Git repository. You can use the VS Code Source Control view or the terminal to manage your code.

## Customization

-   **Dependencies**: Add Python packages or Airflow providers to `requirements.txt`.
-   **Configuration**: Modify `Dockerfile` or `entrypoint.sh` for advanced setups.
