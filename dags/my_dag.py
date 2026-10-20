from datetime import datetime, timedelta
from airflow.decorators import dag, task
from airflow.providers.standard.operators.bash import BashOperator


# Define the DAG using the modern @dag decorator
@dag(
    dag_id="airflow3_modern_dag_example",
    description="An example pipeline utilizing Airflow 3 TaskFlow design",
    start_date=datetime(2026, 1, 1),
    schedule="@daily",
    catchup=False,
    tags=["example", "airflow3"],
    default_args={
        "retries": 2,
        "retry_delay": timedelta(minutes=5),
    }
)
def my_workflow():
    # 1. Standard Operator Task: Fetching raw data using Bash
    extract_data = BashOperator(
        task_id="extract_raw_data",
        bash_command="echo '{\"status\": \"success\", \"records\": 42}'"
    )

    # 2. TaskFlow Python Task: Transform the extracted data
    @task
    def transform_payload(raw_json_string: str) -> dict:
        """Parses the payload and prepares a transformation summary."""
        import json

        data = json.loads(raw_json_string)
        record_count = data.get("records", 0)

        # Airflow 3 natively passes XCom objects when returning dictionaries
        return {
            "processed_at": datetime.utcnow().isoformat(),
            "double_records": record_count * 2
        }

    # 3. TaskFlow Python Task: Load data using the transformed output
    @task
    def load_metrics(metrics: dict):
        """Simulates loading metrics into a warehouse securely."""
        print(f"Loading data processed at: {metrics['processed_at']}")
        print(f"Total computed records: {metrics['double_records']}")

    # Define the execution pipeline flow
    # XCom values are implicitly passed between the decorated functions
    transformed_data = transform_payload(extract_data.output)
    load_metrics(transformed_data)


# Instantiate the DAG at the top level so the DAG processor can discover it
my_workflow()
