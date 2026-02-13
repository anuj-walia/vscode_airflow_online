"""
Airflow Version Launcher — Kubernetes Pod Manager

A Flask app that lets users pick Airflow 2 or 3, then spins up
the corresponding pod in the cluster using the Kubernetes Python client.
"""

import os
import time
import logging
from flask import Flask, render_template, request, jsonify

from kubernetes import client, config
from kubernetes.client.rest import ApiException

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
NAMESPACE = os.environ.get("K8S_NAMESPACE", "airflow-dev")
AIRFLOW_POD_NAME = "airflow-workspace"
AIRFLOW_SERVICE_NAME = "airflow-workspace-svc"
AIRFLOW_NODEPORT = 30088  # Fixed NodePort so the redirect URL is predictable

VERSIONS = {
    "airflow2": {
        "label": "Airflow 2.11.0",
        "branch": "main",
        "image": "airflow-jupyter:airflow2",
        "description": "Stable Airflow 2.x with Webserver UI",
    },
    "airflow3": {
        "label": "Airflow 3.1.7",
        "branch": "airflow3",
        "image": "airflow-jupyter:airflow3",
        "description": "Latest Airflow 3.x with API Server UI",
    },
}

# ---------------------------------------------------------------------------
# Kubernetes client setup
# ---------------------------------------------------------------------------
def get_k8s_clients():
    """Load kubeconfig (in-cluster when deployed, local for dev)."""
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()
    return client.CoreV1Api()


def _pod_manifest(version_key: str) -> client.V1Pod:
    ver = VERSIONS[version_key]
    return client.V1Pod(
        metadata=client.V1ObjectMeta(
            name=AIRFLOW_POD_NAME,
            namespace=NAMESPACE,
            labels={"app": "airflow-workspace", "version": version_key},
        ),
        spec=client.V1PodSpec(
            containers=[
                client.V1Container(
                    name="airflow",
                    image=ver["image"],
                    image_pull_policy="Never",  # local images
                    ports=[client.V1ContainerPort(container_port=8888)],
                    env=[
                        client.V1EnvVar(name="LOAD_EX", value="n"),
                        client.V1EnvVar(name="EXECUTOR", value="SequentialExecutor"),
                    ],
                    volume_mounts=[
                        client.V1VolumeMount(name="dags", mount_path="/opt/airflow/dags"),
                        client.V1VolumeMount(name="logs", mount_path="/opt/airflow/logs"),
                        client.V1VolumeMount(name="plugins", mount_path="/opt/airflow/plugins"),
                    ],
                )
            ],
            restart_policy="Always",
            volumes=[
                client.V1Volume(
                    name="dags",
                    host_path=client.V1HostPathVolumeSource(
                        path="/opt/airflow-shared/dags", type="DirectoryOrCreate"
                    ),
                ),
                client.V1Volume(
                    name="logs",
                    host_path=client.V1HostPathVolumeSource(
                        path="/opt/airflow-shared/logs", type="DirectoryOrCreate"
                    ),
                ),
                client.V1Volume(
                    name="plugins",
                    host_path=client.V1HostPathVolumeSource(
                        path="/opt/airflow-shared/plugins", type="DirectoryOrCreate"
                    ),
                ),
            ],
        ),
    )


def _service_manifest() -> client.V1Service:
    return client.V1Service(
        metadata=client.V1ObjectMeta(
            name=AIRFLOW_SERVICE_NAME,
            namespace=NAMESPACE,
        ),
        spec=client.V1ServiceSpec(
            type="NodePort",
            selector={"app": "airflow-workspace"},
            ports=[
                client.V1ServicePort(
                    port=8888,
                    target_port=8888,
                    node_port=AIRFLOW_NODEPORT,
                    name="jupyter",
                )
            ],
        ),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _delete_airflow_pod(v1: client.CoreV1Api):
    """Delete the existing Airflow pod if it exists."""
    try:
        v1.delete_namespaced_pod(
            name=AIRFLOW_POD_NAME,
            namespace=NAMESPACE,
            body=client.V1DeleteOptions(grace_period_seconds=5),
        )
        logger.info("Deleted existing pod %s", AIRFLOW_POD_NAME)
        # Wait for deletion
        for _ in range(30):
            try:
                v1.read_namespaced_pod(AIRFLOW_POD_NAME, NAMESPACE)
                time.sleep(1)
            except ApiException as e:
                if e.status == 404:
                    break
    except ApiException as e:
        if e.status != 404:
            raise


def _ensure_service(v1: client.CoreV1Api):
    """Create the NodePort service if it doesn't exist."""
    try:
        v1.read_namespaced_service(AIRFLOW_SERVICE_NAME, NAMESPACE)
        logger.info("Service %s already exists", AIRFLOW_SERVICE_NAME)
    except ApiException as e:
        if e.status == 404:
            v1.create_namespaced_service(NAMESPACE, _service_manifest())
            logger.info("Created service %s", AIRFLOW_SERVICE_NAME)
        else:
            raise


def _get_running_version(v1: client.CoreV1Api) -> dict | None:
    """Check if an Airflow pod is currently running and return its info."""
    try:
        pod = v1.read_namespaced_pod(AIRFLOW_POD_NAME, NAMESPACE)
        version_key = pod.metadata.labels.get("version", "unknown")
        phase = pod.status.phase
        ready = False
        if pod.status.container_statuses:
            ready = all(cs.ready for cs in pod.status.container_statuses)
        return {
            "version": version_key,
            "phase": phase,
            "ready": ready,
            "info": VERSIONS.get(version_key, {}),
        }
    except ApiException as e:
        if e.status == 404:
            return None
        raise


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    v1 = get_k8s_clients()
    running = _get_running_version(v1)
    return render_template(
        "index.html",
        versions=VERSIONS,
        running=running,
        nodeport=AIRFLOW_NODEPORT,
    )


@app.route("/launch", methods=["POST"])
def launch():
    version_key = request.form.get("version")
    if version_key not in VERSIONS:
        return jsonify({"error": f"Unknown version: {version_key}"}), 400

    v1 = get_k8s_clients()

    # Check if same version is already running
    running = _get_running_version(v1)
    if running and running["version"] == version_key and running["ready"]:
        return jsonify({
            "status": "already_running",
            "redirect_url": f"http://localhost:{AIRFLOW_NODEPORT}",
        })

    # Delete existing pod (if switching versions)
    _delete_airflow_pod(v1)

    # Create new pod
    v1.create_namespaced_pod(NAMESPACE, _pod_manifest(version_key))
    logger.info("Created pod %s with image %s", AIRFLOW_POD_NAME, VERSIONS[version_key]["image"])

    # Ensure service exists
    _ensure_service(v1)

    return jsonify({
        "status": "launching",
        "version": version_key,
        "message": f"Starting {VERSIONS[version_key]['label']}...",
    })


@app.route("/status")
def status():
    v1 = get_k8s_clients()
    running = _get_running_version(v1)
    if not running:
        return jsonify({"status": "not_found"})

    if running["ready"]:
        return jsonify({
            "status": "ready",
            "version": running["version"],
            "redirect_url": f"http://localhost:{AIRFLOW_NODEPORT}",
        })

    return jsonify({
        "status": "starting",
        "phase": running["phase"],
        "version": running["version"],
    })


@app.route("/stop", methods=["POST"])
def stop():
    v1 = get_k8s_clients()
    _delete_airflow_pod(v1)
    return jsonify({"status": "stopped"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
