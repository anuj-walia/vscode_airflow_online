"""
Starter DAG for the Airflow 3 workspace.

Copied into your DAGs folder the first time the workspace starts, and never
overwritten afterwards. Edit or delete it freely.

Airflow 3 notes worth knowing before you copy patterns from older examples:
  * BashOperator moved to the `standard` provider.
  * `execution_date` is gone; use `logical_date`, or `data_interval_start` and
    `data_interval_end` for the window a run covers.
  * Tasks run in a separate process that reaches Airflow over the Execution
    API, so they cannot query the metadata database directly.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import dag, task


@dag(
    dag_id="example_airflow3_workspace",
    description="Starter TaskFlow pipeline for the Airflow 3 developer workspace",
    start_date=datetime(2026, 1, 1),
    schedule=None,
    catchup=False,
    tags=["example", "workspace"],
    default_args={"retries": 1, "retry_delay": timedelta(minutes=1)},
)
def example_airflow3_workspace():
    extract = BashOperator(
        task_id="extract",
        bash_command='echo \'{"records": 42}\'',
    )

    @task
    def transform(raw: str) -> dict:
        import json

        return {"doubled": json.loads(raw)["records"] * 2}

    @task
    def load(metrics: dict) -> None:
        print(f"loaded {metrics['doubled']} records")

    load(transform(extract.output))


example_airflow3_workspace()
