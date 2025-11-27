from airflow.decorators import dag,task
from datetime import datetime


@dag(start_date=datetime(2025, 11, 1), schedule=None, catchup=False)
def my_first_dag():

    @task
    def python_task():
        print("just some task")
        return "Hello from python task"

    @task.bash
    def bash_task(msg):
        print("executing bash task")
        return f"echo {msg}"

    bash_task(python_task())

my_first_dag()