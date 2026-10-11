"""
analysis/plot_o1_baseline.py — Generates Gate 1 baseline evidence figure.

Plots:
Panel 1: Offered Load (rps) vs Desired & Ready Replicas over time.
Panel 2: End-to-End Latency (ms) with Frozen SLO Threshold (100ms) highlighted.
Highlights the Reactive Autoscaling Lag window and resulting SLO breaches.
Outputs 300 dpi publication PNG to figures/o1_baseline_reactive_lag.png.
"""

import os
import pandas as pd
import matplotlib.pyplot as plt

def main():
    csv_path = "results/raw/o1/step_load_telemetry.csv"
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"Missing {csv_path}")
        
    df = pd.read_csv(csv_path)
    
    os.makedirs("figures", exist_ok=True)
    
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 7), sharex=True, gridspec_kw={'height_ratios': [1.2, 1]})
    
    # ── Panel 1: Offered Load & Replicas ─────────────────────────────────────
    color_load = "#1f77b4"
    color_desired = "#ff7f0e"
    color_ready = "#2ca02c"
    
    ax1.plot(df["elapsed_s"], df["offered_rate"], label="Offered Demand (req/s)", color=color_load, linewidth=2.5)
    ax1.set_ylabel("Offered Load (req/s)", color=color_load, fontsize=11, fontweight="bold")
    ax1.tick_params(axis='y', labelcolor=color_load)
    ax1.grid(True, linestyle="--", alpha=0.5)
    
    # Twin axis for replicas
    ax1_rep = ax1.twinx()
    ax1_rep.step(df["elapsed_s"], df["desired_replicas"], label="Desired Replicas (HPA)", color=color_desired, linestyle="--", linewidth=1.8, where="post")
    ax1_rep.step(df["elapsed_s"], df["ready_replicas"], label="Ready Replicas", color=color_ready, linewidth=2.2, where="post")
    ax1_rep.set_ylabel("Pod Replicas", color="#333333", fontsize=11, fontweight="bold")
    ax1_rep.set_ylim(0, 12)
    
    # Highlight Demand Onset and Reactive Lag
    ax1.axvline(x=40.0, color="#d62728", linestyle=":", linewidth=2, label="Load Onset (t_onset = 40s)")
    ax1.axvspan(40.0, 120.0, color="#d62728", alpha=0.12, label="Reactive Scaling Lag Window (~80s)")
    
    # Combine legends for ax1
    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax1_rep.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper left", framealpha=0.9, fontsize=9)
    ax1.set_title("O1 Problem Validation: Reactive Kubernetes HPA (CPU) Under Step Load", fontsize=13, fontweight="bold", pad=12)
    
    # ── Panel 2: Latency & SLO Violations ────────────────────────────────────
    color_lat = "#9467bd"
    ax2.plot(df["elapsed_s"], df["probe_latency_ms"], label="End-to-End Latency (ms)", color=color_lat, linewidth=1.8, marker="o", markersize=4)
    ax2.axhline(y=100.0, color="#d62728", linestyle="-.", linewidth=2, label="Frozen SLO Latency Threshold (100 ms)")
    
    # Highlight SLO violation points
    viol = df[df["slo_violation"] == 1]
    if not viol.empty:
        ax2.scatter(viol["elapsed_s"], viol["probe_latency_ms"], color="#d62728", s=60, zorder=5, label="SLO Violation Breach")
        
    ax2.set_xlabel("Elapsed Experiment Time (seconds)", fontsize=11, fontweight="bold")
    ax2.set_ylabel("Latency (ms)", fontsize=11, fontweight="bold")
    ax2.set_ylim(0, max(220.0, df["probe_latency_ms"].max() * 1.15))
    ax2.grid(True, linestyle="--", alpha=0.5)
    ax2.legend(loc="upper right", framealpha=0.9, fontsize=9)
    
    plt.tight_layout()
    output_png = "figures/o1_baseline_reactive_lag.png"
    plt.savefig(output_png, dpi=300)
    plt.close()
    print(f"[Plot Complete] 300 dpi baseline figure written to {output_png}")

if __name__ == "__main__":
    main()
