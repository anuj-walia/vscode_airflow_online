# Airflow 3.x Migration Guide

This document explains the changes made to support Apache Airflow 3.1.1 in this Docker project.

## Key Architectural Changes in Airflow 3.x

### 1. Service-Oriented Architecture
Airflow 3.x introduces a **service-oriented architecture** where:
- The **API Server** is now the sole gateway to the metadata database
- Workers and tasks no longer directly access the database
- All runtime interactions (state transitions, heartbeats, XComs) go through the Task Execution API

### 2. New API Server Component
- **Purpose**: Centralized access point for all database operations
- **Port**: 9091 (default)
- **Access**: Available via Jupyter Launcher as "Airflow API Server"
- **Configuration**: `AIRFLOW__API__BASE_URL` environment variable

### 3. Database Migration Command
- **Old (Airflow 2.x)**: `airflow db init`
- **New (Airflow 3.x)**: `airflow db migrate`

## Changes Made to This Project

### Dockerfile Updates
1. **Added API Server Configuration**:
   ```dockerfile
   ENV AIRFLOW__API__BASE_URL=http://localhost:8888/airflow-api
   ```

2. **Added API Server to Jupyter Proxy**:
   - New launcher icon for "Airflow API Server"
   - Command: `airflow api-server --port {port}`

3. **Exposed API Server Port**:
   ```dockerfile
   EXPOSE 8888 8080 9091 8999
   ```

### Entrypoint Script Updates
- Changed database initialization from `airflow db init` to `airflow db migrate`

### README Updates
- Updated to reflect Airflow 3.1.1
- Documented the new API Server component
- Explained the service-oriented architecture

## Compatibility Notes

### ✅ Still Supported
- **SQLite with SequentialExecutor**: Still works in Airflow 3.x for development
- **Python 3.11**: Fully supported
- **Existing DAGs**: TaskFlow API DAGs work without changes

### ⚠️ Breaking Changes
- Direct database access from task code is no longer allowed
- Tasks must use the API server for all metadata operations
- Some internal APIs have changed

## Testing the Changes

1. **Build the image**:
   ```bash
   docker compose build
   ```

2. **Start the container**:
   ```bash
   docker compose up
   ```

3. **Verify components**:
   - JupyterLab: http://localhost:8888
   - Airflow Webserver: Click launcher icon
   - **Airflow API Server**: Click launcher icon (new!)
   - Scheduler Logs: Click launcher icon

## Benefits of Airflow 3.x

1. **Enhanced Security**: Tasks can't directly manipulate the database
2. **Better Scalability**: API server can be scaled independently
3. **Improved Isolation**: Workers are strictly isolated from the database
4. **Modern Architecture**: Cleaner separation of concerns

## Resources

- [Airflow 3.0 Release Notes](https://airflow.apache.org/docs/apache-airflow/stable/release_notes.html)
- [Migration Guide](https://airflow.apache.org/docs/apache-airflow/stable/installation/upgrading.html)
