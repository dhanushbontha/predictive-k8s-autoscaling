import math
import os
import random
import time
from typing import Optional, Tuple
from locust import HttpUser, LoadTestShape, between, events, task

# Configurable target URL, defaults to localhost:8000
DEFAULT_BASE_URL = os.getenv("TARGET_BASE_URL", "http://127.0.0.1:8000")


def get_env_int(name: str, default: int) -> int:
    val = os.getenv(name)
    if val is not None:
        try:
            return int(val)
        except ValueError:
            pass
    return default


def get_env_float(name: str, default: float) -> float:
    val = os.getenv(name)
    if val is not None:
        try:
            return float(val)
        except ValueError:
            pass
    return default


class TargetAppUser(HttpUser):
    """
    Simulated user sending load specifically to the target FastAPI application's /api/work endpoint.
    Think time is kept between 0.1s and 0.4s to maintain consistent client concurrency.
    """
    host = DEFAULT_BASE_URL
    wait_time = between(0.1, 0.4)

    @task(10)
    def cpu_workload_default(self):
        """Standard CPU-intensive computation (100,000 iterations)."""
        self.client.get("/api/work", name="/api/work (default 100k)")

    @task(3)
    def cpu_workload_variable(self):
        """Moderate computational load with variable iterations (150k - 250k)."""
        iterations = random.randint(150_000, 250_000)
        self.client.get(
            f"/api/work?iterations={iterations}",
            name="/api/work (variable 150k-250k)",
        )


class WorkloadShapeConfig:
    """Encapsulates configuration parsing and user calculations for all 5 workload profiles."""

    def __init__(self):
        # General Profile Selection & Duration
        self.profile = os.getenv("WORKLOAD_PROFILE", "stable").strip().lower()
        self.duration_seconds = get_env_int("EXPERIMENT_DURATION", 120)

        # Baseline / User Counts
        self.user_count = get_env_int("USER_COUNT", 6)
        self.spawn_rate = get_env_float("SPAWN_RATE", 2.0)
        self.low_users = get_env_int("LOW_USERS", 2)
        self.high_users = get_env_int("HIGH_USERS", 10)

        # Periodic Profile Settings
        self.periodic_period = get_env_int("PERIODIC_PERIOD", 40)
        self.periodic_high_duration = get_env_int("PERIODIC_HIGH_DURATION", 20)

        # Gradual Profile Settings (Stages)
        self.gradual_stages = get_env_int("GRADUAL_STAGES", 4)

        # Bursty Profile Settings
        self.burst_period = get_env_int("BURST_PERIOD", 35)
        self.burst_duration = get_env_int("BURST_DURATION", 10)
        self.burst_users = get_env_int("BURST_USERS", 12)

        # Noisy Profile Settings & Seed
        self.noisy_min_users = get_env_int("NOISY_MIN_USERS", 2)
        self.noisy_max_users = get_env_int("NOISY_MAX_USERS", 12)
        self.noisy_interval = get_env_int("NOISY_INTERVAL", 10)
        seed_str = os.getenv("NOISY_SEED", "42")
        self.seed = int(seed_str) if seed_str.isdigit() else 42
        self._noisy_rng = random.Random(self.seed)
        self._noisy_schedule = self._generate_noisy_schedule()

    def _generate_noisy_schedule(self):
        """Generates reproducible step-wise user targets for noisy profile."""
        schedule = []
        t = 0
        while t < self.duration_seconds + 300:
            users = self._noisy_rng.randint(self.noisy_min_users, self.noisy_max_users)
            schedule.append((t, users))
            t += self.noisy_interval
        return schedule

    def calculate_tick(self, run_time: float) -> Optional[Tuple[int, float]]:
        """
        Determines user count and spawn rate based on the elapsed run_time.
        Returns None when the experiment duration has elapsed to terminate the test.
        """
        if run_time > self.duration_seconds:
            return None

        # Profile A: Stable Load
        if self.profile in ("stable", "a"):
            return self.user_count, self.spawn_rate

        # Profile B: Periodic Load
        elif self.profile in ("periodic", "b"):
            cycle_pos = int(run_time) % self.periodic_period
            if cycle_pos < self.periodic_high_duration:
                return self.high_users, self.spawn_rate
            else:
                return self.low_users, self.spawn_rate

        # Profile C: Gradually Increasing Load
        elif self.profile in ("gradual", "c"):
            stage_duration = max(1.0, float(self.duration_seconds) / max(1, self.gradual_stages))
            current_stage = min(self.gradual_stages - 1, int(run_time // stage_duration))
            step_size = (self.high_users - self.low_users) / max(1, (self.gradual_stages - 1))
            current_users = int(round(self.low_users + (current_stage * step_size)))
            return max(1, current_users), self.spawn_rate

        # Profile D: Bursty Load
        elif self.profile in ("bursty", "d"):
            cycle_pos = int(run_time) % self.burst_period
            if cycle_pos < self.burst_duration:
                # Active high-traffic burst (fast ramp)
                return self.burst_users, max(self.spawn_rate, 4.0)
            else:
                # Quiet baseline period
                return self.low_users, self.spawn_rate

        # Profile E: Noisy Load
        elif self.profile in ("noisy", "e"):
            # Step-wise reproducible random variation
            step_idx = int(run_time // self.noisy_interval)
            step_idx = min(step_idx, len(self._noisy_schedule) - 1)
            target_users = self._noisy_schedule[step_idx][1]
            return target_users, self.spawn_rate

        # Fallback to stable if profile unrecognized
        return self.user_count, self.spawn_rate


class ConfigurableWorkloadShape(LoadTestShape):
    """
    Locust LoadTestShape controller that drives the simulated user count
    dynamically across time according to the selected profile.
    """
    def __init__(self):
        super().__init__()
        self.cfg = WorkloadShapeConfig()

    def tick(self) -> Optional[Tuple[int, float]]:
        run_time = self.get_run_time()
        return self.cfg.calculate_tick(run_time)
