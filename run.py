"""
run.py — Universal Master Automation Entrypoint for predictive-k8s-autoscaling.

Provides unified commands across Windows (PowerShell/CMD) and Linux:
- python run.py setup       : Sets up Minikube, KEDA, and Prometheus stack
- python run.py test        : Runs full pytest suite (app + metric formulas)
- python run.py calibrate   : Executes capacity calibration at 1, 4, 10 replicas
- python run.py o1-baseline : Runs HPA step-load trial and produces Gate 1 evidence plot
- python run.py analyze     : Runs statistical analysis pipeline and generates publication figures
- python run.py demo        : Runs 10-minute live demonstration comparison
- python run.py reproduce   : Clean-room automated reproduction pipeline
"""

import sys
import os
import subprocess
import argparse

def run_cmd(cmd, check=True):
    print(f"\n>>> Running: {cmd}")
    res = subprocess.run(cmd, shell=True, check=check)
    if res.returncode != 0 and check:
        sys.exit(res.returncode)
    return res.returncode

def setup():
    print("[run.py] Executing cluster setup ...")
    if os.name == 'nt':
        run_cmd("powershell -ExecutionPolicy Bypass -File .\\scripts\\setup-cluster.ps1")
    else:
        run_cmd("bash ./scripts/setup-cluster.sh")

def test():
    print("[run.py] Running unit tests ...")
    env = os.environ.copy()
    env["PYTHONPATH"] = "."
    subprocess.run(["pytest", "tests/", "-v"], env=env, check=True)

def calibrate():
    print("[run.py] Running capacity calibration ...")
    env = os.environ.copy()
    env["PYTHONPATH"] = "."
    subprocess.run([sys.executable, "runner/calibrate.py"], env=env, check=True)

def o1_baseline():
    print("[run.py] Running O1 baseline HPA step-load experiment ...")
    env = os.environ.copy()
    env["PYTHONPATH"] = "."
    subprocess.run([sys.executable, "runner/run_step_hpa.py"], env=env, check=True)
    subprocess.run([sys.executable, "analysis/plot_o1_baseline.py"], env=env, check=True)

def analyze():
    print("[run.py] Running analysis pipeline ...")
    env = os.environ.copy()
    env["PYTHONPATH"] = "."
    subprocess.run([sys.executable, "analysis/plot_o1_baseline.py"], env=env, check=True)

def main():
    parser = argparse.ArgumentParser(description="predictive-k8s-autoscaling unified runner")
    parser.add_argument("command", choices=["setup", "test", "calibrate", "o1-baseline", "analyze", "demo", "reproduce"],
                        help="Command action to execute")
    args = parser.parse_args()
    
    if args.command == "setup":
        setup()
    elif args.command == "test":
        test()
    elif args.command == "calibrate":
        calibrate()
    elif args.command == "o1-baseline":
        o1_baseline()
    elif args.command == "analyze":
        analyze()
    elif args.command == "demo":
        print("[run.py] Demo target will be populated in P7.")
    elif args.command == "reproduce":
        print("[run.py] Reproduce target will be populated in P7.")

if __name__ == "__main__":
    main()
