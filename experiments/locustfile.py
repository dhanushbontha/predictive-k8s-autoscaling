import os
import random
from locust import HttpUser, task, between

# Configurable target URL, defaults to localhost:8000
DEFAULT_BASE_URL = os.getenv("TARGET_BASE_URL", "http://127.0.0.1:8000")


class TargetAppUser(HttpUser):
    """
    Simulated user sending load to the target FastAPI application.
    Workload intensity is controlled via Locust's simulated user count and spawn rate.
    """
    host = DEFAULT_BASE_URL
    # Simulated think time between user requests (0.1s to 0.5s)
    wait_time = between(0.1, 0.5)

    @task(10)
    def cpu_workload_default(self):
        """
        Primary task: standard CPU-intensive computation (100,000 iterations).
        Triggers CPU usage to evaluate reactive HPA and predictive autoscaling.
        """
        self.client.get("/api/work", name="/api/work (default 100k)")

    @task(3)
    def cpu_workload_variable(self):
        """
        Secondary task: sends moderate or higher computational load
        with variable iterations (150,000 to 300,000 iterations).
        """
        iterations = random.randint(150_000, 300_000)
        self.client.get(
            f"/api/work?iterations={iterations}",
            name="/api/work (variable 150k-300k)",
        )

    @task(1)
    def health_check(self):
        """
        Lightweight baseline check to verify the service remains responsive under load.
        """
        self.client.get("/health", name="/health")
