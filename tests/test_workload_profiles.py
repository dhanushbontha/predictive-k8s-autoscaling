import os
import sys
from pathlib import Path
import pytest

# Ensure workspace root is in sys.path
workspace_root = Path(__file__).resolve().parent.parent
if str(workspace_root) not in sys.path:
    sys.path.insert(0, str(workspace_root))

from experiments.locustfile import WorkloadShapeConfig, TargetAppUser


def test_default_configuration():
    # Verify default environment parsing
    with pytest.MonkeyPatch.context() as mp:
        mp.delenv("WORKLOAD_PROFILE", raising=False)
        mp.delenv("EXPERIMENT_DURATION", raising=False)
        mp.delenv("USER_COUNT", raising=False)
        mp.delenv("SPAWN_RATE", raising=False)

        cfg = WorkloadShapeConfig()
        assert cfg.profile == "stable"
        assert cfg.duration_seconds == 120
        assert cfg.user_count == 6
        assert cfg.spawn_rate == 2.0


def test_profile_a_stable():
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("WORKLOAD_PROFILE", "stable")
        mp.setenv("USER_COUNT", "8")
        mp.setenv("SPAWN_RATE", "3.0")
        mp.setenv("EXPERIMENT_DURATION", "60")

        cfg = WorkloadShapeConfig()
        # Should stay at 8 users throughout
        assert cfg.calculate_tick(0.0) == (8, 3.0)
        assert cfg.calculate_tick(30.0) == (8, 3.0)
        assert cfg.calculate_tick(59.9) == (8, 3.0)
        # Should terminate after 60s
        assert cfg.calculate_tick(60.1) is None


def test_profile_b_periodic():
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("WORKLOAD_PROFILE", "periodic")
        mp.setenv("PERIODIC_PERIOD", "40")
        mp.setenv("PERIODIC_HIGH_DURATION", "20")
        mp.setenv("LOW_USERS", "2")
        mp.setenv("HIGH_USERS", "10")

        cfg = WorkloadShapeConfig()
        # High phase (t=0 to 19s)
        assert cfg.calculate_tick(5.0) == (10, 2.0)
        assert cfg.calculate_tick(19.0) == (10, 2.0)
        # Low phase (t=20 to 39s)
        assert cfg.calculate_tick(20.0) == (2, 2.0)
        assert cfg.calculate_tick(35.0) == (2, 2.0)
        # Next cycle high phase (t=40 to 59s)
        assert cfg.calculate_tick(42.0) == (10, 2.0)


def test_profile_c_gradual():
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("WORKLOAD_PROFILE", "gradual")
        mp.setenv("EXPERIMENT_DURATION", "100")
        mp.setenv("GRADUAL_STAGES", "4")
        mp.setenv("LOW_USERS", "2")
        mp.setenv("HIGH_USERS", "11")

        cfg = WorkloadShapeConfig()
        # Stage 0 (t=0 to 24s) -> 2 users
        assert cfg.calculate_tick(10.0)[0] == 2
        # Stage 1 (t=25 to 49s) -> 5 users
        assert cfg.calculate_tick(30.0)[0] == 5
        # Stage 2 (t=50 to 74s) -> 8 users
        assert cfg.calculate_tick(60.0)[0] == 8
        # Stage 3 (t=75 to 99s) -> 11 users
        assert cfg.calculate_tick(80.0)[0] == 11
        # Done at 100s
        assert cfg.calculate_tick(101.0) is None


def test_profile_d_bursty():
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("WORKLOAD_PROFILE", "bursty")
        mp.setenv("BURST_PERIOD", "30")
        mp.setenv("BURST_DURATION", "10")
        mp.setenv("BURST_USERS", "15")
        mp.setenv("LOW_USERS", "3")

        cfg = WorkloadShapeConfig()
        # Burst active (t < 10)
        users, spawn = cfg.calculate_tick(5.0)
        assert users == 15
        assert spawn >= 4.0
        # Quiet period (10 <= t < 30)
        assert cfg.calculate_tick(15.0) == (3, 2.0)
        # Next burst (30 <= t < 40)
        users2, _ = cfg.calculate_tick(32.0)
        assert users2 == 15


def test_profile_e_noisy_seed_reproducibility():
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("WORKLOAD_PROFILE", "noisy")
        mp.setenv("NOISY_SEED", "12345")
        mp.setenv("NOISY_MIN_USERS", "2")
        mp.setenv("NOISY_MAX_USERS", "10")
        mp.setenv("NOISY_INTERVAL", "5")

        cfg1 = WorkloadShapeConfig()
        cfg2 = WorkloadShapeConfig()

        # Both instances with same seed must yield exact same user counts across time
        for t in [0.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0]:
            assert cfg1.calculate_tick(t) == cfg2.calculate_tick(t)

        # Different seed yields different pattern
        mp.setenv("NOISY_SEED", "99999")
        cfg3 = WorkloadShapeConfig()
        ticks1 = [cfg1.calculate_tick(t)[0] for t in [0.0, 5.0, 10.0, 15.0, 20.0]]
        ticks3 = [cfg3.calculate_tick(t)[0] for t in [0.0, 5.0, 10.0, 15.0, 20.0]]
        assert ticks1 != ticks3


def test_target_app_user_tasks():
    # Verify tasks only target /api/work and do not target /metrics or /health
    # Note: Locust stores tasks with weights as repeated items in User.tasks
    unique_tasks = set(TargetAppUser.tasks)
    assert len(unique_tasks) == 2
    task_names = {t.__name__ for t in unique_tasks}
    assert "cpu_workload_default" in task_names
    assert "cpu_workload_variable" in task_names
    assert "health_check" not in task_names
