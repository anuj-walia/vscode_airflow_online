# Docker Project: Airflow + Jupyter + VS Code

This project provides a complete Docker-based development environment for Apache Airflow, integrated with JupyterLab and VS Code (code-server).
This a handy way of working with airflow in banks , insurance companies where you might not have access to docker on your local machine and testing and debugging dags becomes difficult. 
But the platform teams can use this image to create a sort of workspace as a service to give Data engineers an environment to test and debug their dags.

When i was running platforms in the bank i used to work , giving this to our users was a big hit and created a low resistance path to migrate to airflow from legacy or impropmptu schedulers and also helped in reducing End User Computes(EUCs). 
Our onboarding rates exploded after this.


## Features

-   **Apache Airflow 3.1.1**:
    -   Modern service-oriented architecture with dedicated API server.
    -   Configured with `SequentialExecutor` and SQLite.
    -   **API Server**: New in Airflow 3.x - serves as the sole gateway to the metadata database.
    -   Webserver, API Server, and Scheduler logs accessible via Jupyter Launcher.
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
    -   **Airflow API Server**: Click the "Airflow API Server" icon (new in Airflow 3.x).
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
