#!/bin/bash
set -e


# Activate the virtual environment
source /opt/airflow_venv/bin/activate

# Initialize Airflow DB
if [ ! -f "/opt/airflow/airflow.db" ]; then
    echo "Initializing Airflow DB..."
    # Airflow 3.x uses 'db migrate' instead of 'db init'
    airflow db migrate
fi

# Create Admin User for SimpleAuthManager (Airflow 3.x)
# We pre-populate the password file to ensure admin/admin credentials work
echo "Creating Admin User (admin/admin)..."
echo '{"admin": "admin"}' > /opt/airflow/simple_auth_manager_passwords.json.generated

# Start JupyterLab
echo "Starting JupyterLab..."
exec jupyter lab --ip=0.0.0.0 --port=8888 --no-browser --allow-root --NotebookApp.token=''