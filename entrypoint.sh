#!/bin/bash
set -e

# Activate the virtual environment
source /opt/airflow_venv/bin/activate

# Initialize Airflow DB
if [ ! -f "/opt/airflow/airflow.db" ]; then
    echo "Initializing Airflow DB..."
    airflow db init
    
    echo "Creating Admin User..."
    airflow users create \
        --username admin \
        --firstname Admin \
        --lastname User \
        --role Admin \
        --email admin@example.com \
        --password admin
fi

# Start JupyterLab
echo "Starting JupyterLab..."
exec jupyter lab --ip=0.0.0.0 --port=8888 --no-browser --allow-root --NotebookApp.token=''
