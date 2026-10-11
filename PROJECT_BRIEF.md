# PROJECT_BRIEF.md — CLOUD-03 Predictive Autoscaling (Prophet + KEDA vs reactive HPA)

> **How to use this file**
> 1. Put it in the repo root (`predictive-k8s-autoscaling`) and also drop the capstone spec PDF and the paper draft (.docx) into `docs/`.
> 2. Start the agent with: *"Read PROJECT_BRIEF.md fully, then docs/ (spec PDF + paper draft). Confirm you understood by summarising the locked design in 10 lines. Do nothing else yet."*
> 3. Then paste the **Phase prompts (P0 → P7)** from the bottom of this file **one at a time**. Review each gate before pasting the next.
> 4. Model tip: Opus 4.6 for P0, P2 (design), P6, P7 (analysis + paper text). Sonnet 4.6 for the scaffolding/implementation phases (P1, P3, P4, P5).
> 5. Run long experiment campaigns in a normal terminal (not inside the chat), so they survive agent context resets. The agent writes the runner; you launch it.

---

# PART 1 — MASTER BRIEF (the agent must obey this for the whole project)

## 1. Your role and the one rule that matters most

You are the lead engineer and research assistant for a 3-student KL University capstone (CLOUD-03) whose output is **(a) a working, demonstrable predictive-autoscaling system, (b) a reproducible evidence package, and (c) the results for an IEEE-style research paper.**

**Integrity rules (non-negotiable):**
- Every number that appears in any table, figure, README or paper text must be **computed by a script from raw data files stored in the repo** (`results/raw/`). Never type a result by hand. Never "estimate", "expect", simulate-and-present-as-real, or fill placeholders with plausible values.
- If an experiment fails, is noisy, or predictive scaling does **not** beat HPA, **report that exactly as measured.** The paper is deliberately framed as "when does prediction help and when does it not", so mixed or negative results are acceptable and publishable. Fabricated or cherry-picked results are not.
- Do not silently change the locked design (Section 3). If something is infeasible, stop and tell me what you found, with evidence, and propose options. I decide.
- Never claim causation. The paper only claims associations between naturally occurring forecast error / provisioning delay and outcomes.
- If you are unsure about a tool version, API, flag or KEDA/Prophet behaviour, **check the official docs or run a quick experiment** rather than guessing. Pin every version you end up using.
- If something in the spec PDF is internally inconsistent (e.g. KPI-2 "cost per successful transaction" is labelled "higher is better, ≥ reference + 3 pp", which looks like a template artifact), do **not** silently pick an interpretation. Record it in `docs/SPEC_NOTES.md` with both readings, report both, and flag it for my mentor.

## 2. Project context

- Capstone spec: `docs/CLOUD-03_Predictive_Autoscaling_with_Load_Forecasting.pdf`. Paper draft: `docs/Evaluating_the_Limits_of_Predictive_Kubernetes_Autoscaling_updated.docx` (covers Introduction → Methodology; Experimental Setup, Results, Discussion, Limitations, Conclusion are still to be written **from the data**).
- Repo: https://github.com/dhanushbontha/predictive-k8s-autoscaling.git
- Tools named in the spec: Kubernetes, KEDA, Prophet / LSTM, k6 or Locust, Prometheus. Isolated local cluster is allowed (kind / k3d / minikube). No production dependency.
- Spec stages you must cover: O1 baseline HPA + problem validation → O2 reproducible forecasting reference (compare ≥ 2 forecasters, documented trade-off) → O3 predictive scaler (KEDA + forecast) → O4 integrated system (controlled load tests reactive vs predictive) → O5 independent acceptance (AC-1…AC-4, KPI-1…KPI-6, negative tests NT1…NT5).
- **A notebook, isolated model, or dashboard alone does not count.** The deliverable is the whole workflow: load → metrics → forecast → KEDA scaling → measurement → analysis, runnable by a documented command.

## 3. LOCKED research design (from the paper draft — do not change without asking me)

**Research question:** Under what workload conditions does predictive autoscaling give a practical advantage over reactive HPA, considering forecast error and real Kubernetes scaling/provisioning behaviour?
- RQ1: effectiveness across workload dynamics vs HPA. RQ2: relation between forecast accuracy and autoscaling/application outcomes. RQ3: influence of real scaling/pod provisioning delay on the benefit.
- H1: benefit larger for sufficiently predictable workloads. H2: higher forecast error ↔ reduced predictive effectiveness. H3: benefit depends on relation between the fixed forecast lead time and observed provisioning/scaling delay. **No direction is assumed in advance; hypotheses are tested as associations only.**

**Fixed parameters:** forecast horizon = **60 s**; scored measurement window = **300 s** per run (an unscored warm-up/reset phase comes before it and an unscored cool-down after it); **5 repetitions per (pattern x policy)**; same application, same workload, same resource envelope, same repetitions for both policies. (These were updated from the draft's original 180 s; the draft has been edited to match.)

**Five workload patterns:** stable, periodic, gradually changing, bursty, noisy. Each must be defined by an explicit generative spec (parameters + seed) in `workloads/SPEC.md`.

**Policies compared (exactly two arms):**
- Arm A (reactive baseline): native Kubernetes HPA with default-style CPU-utilisation scaling (spec O1 says "default HPA").
- Arm B (predictive): Prophet forecast service → Prometheus metric → KEDA `ScaledObject` (Prometheus scaler) scaling ahead of demand. Forecast trigger only in the main experiment.
- **Known limitation to carry into the paper:** KEDA creates and manages an HPA internally, so A-vs-B differs in scaling metric and controller path as well as in the use of forecasting. We deliberately run only two arms; this must be stated in the limitations, and the fairness config (rule 2) must make everything else identical.

**Metrics (names as in the paper):**
- Forecast: MAE, RMSE (eqs. 1–2). Also report sMAPE or MASE (request rate can approach zero), plus forecast latency (time to fit + predict).
- Application: P95, P99 latency, SLO violations, total / successful / failed requests.
- Scaling: replica count, peak replicas, scale-out events, CPU utilisation.
- Delays (paper eqs. 3–5): `D_E2E = t_ready − t0`, `D_provision = t_ready − t_creation`, `D_detect+sched = t_creation − t0`, with `t0` = experiment start timestamp, `t_creation` = pod `metadata.creationTimestamp`, `t_ready` = pod Ready-condition transition time. Keep these definitions exactly.
- **Additional required logging (I want to add this to the paper):** log the **load-onset timestamp(s)** produced by the generator (start of each ramp/burst/step). Because predictive scaling can create pods *before* demand starts, `t_creation − t0` can be meaningless or negative when `t0` is experiment start. Compute both the paper's metrics **and** onset-relative lead/lag (`t_ready − t_onset`; negative = capacity was ready before demand). Also compute **lead-time slack** = 60 s − (`D_provision`) for H3. Note that Kubernetes API timestamps have 1-second resolution; state this as a limitation.
- Resource/cost KPIs: define precisely in `docs/METRICS.md` before running: per-pod sustainable capacity (calibrated, see P1), `provisioned(t) = ready_replicas(t) × capacity`, `overprovisioned pod-hours = ∫ max(0, provisioned − offered)`, `underprovisioned = ∫ max(0, offered − provisioned)`, `cost per successful request = pod-seconds (or CPU-seconds requested) / successful requests`.
- **SLO definition must be set once, from baseline calibration runs, written to `docs/SLO.md`, and frozen before any A-vs-B comparison.** (e.g. P95 ≤ k × steady-state P95 and error rate ≤ 1 % — choose k from data and justify.) Never tune the SLO after seeing comparison results.

## 4. Experimental-validity requirements (these decide whether the paper survives review)

1. **Open-loop load generation.** Use k6 `constant-arrival-rate` / `ramping-arrival-rate` executors (or Locust with a fixed-rate shaper that is verified to hold the offered rate). Closed-loop virtual users throttle themselves when the service slows, which hides exactly the latency we are measuring. Verify offered rate vs achieved rate in every run and log it.
2. **Fairness of controllers (AC-4, frozen resource envelope):** identical app image, resource requests/limits, minReplicas, maxReplicas, HPA `behavior` (scaleUp/scaleDown policies + stabilisation windows), polling/sync interval (set HPA sync and KEDA `pollingInterval` equal or document the difference), node capacity, and Prometheus scrape interval. Put all of this in one versioned config diff table.
3. **Clean state between runs:** reset to the same starting replicas, wait for stable readiness, check no Pending pods, check node headroom (Pending pods due to full nodes would contaminate `D_provision`). Pre-pull images on all nodes (or document if not). Fail the run automatically if the precondition check fails; never include invalid runs silently — keep them in `results/invalid/` with the reason.
4. **Order and repetition:** **5 repetitions per (pattern x policy) = 50 final runs** (block 1). An optional block 2 (5 more repetitions per condition, 100 runs total) is run only if time allows, with its own pre-hashed seeds; the decision to run it must not depend on which policy looks better, and block-1 results must always be reported separately as well as combined. Randomise/interleave the run order to avoid drift in the host machine. The runner must be **resumable** and append-only.
5. **Dev vs acceptance split (spec AC-3):** use separate seed sets. `dev` seeds for tuning Prophet/KEDA settings; `acceptance` seeds (generate 10, hash them **before tuning**) are used exactly once: the first 5 for block 1, the other 5 only for the optional block 2. No parameter changes after acceptance data is seen. Record the hash/date in the evidence manifest.
6. **Prophet specifics:** runs are only 300 s and patterns repeat in seconds, so Prophet's default daily/weekly/yearly seasonality is wrong here. Disable them and add custom seasonality with `period` expressed in days (e.g. 60 s = 60/86400) when the pattern is periodic. Decide and document: history window, refit cadence, how the history for each pattern is produced (**separate warm-up history trace from the same pattern generator but a different seed — never from the evaluation window**), and fallback if the history is too short. Measure forecast latency and count it against the 60 s lead time.
7. **Synthetic-vs-real (spec NT-3):** validate that each synthetic pattern is plausible by comparing it to at least one public real trace (e.g. NASA-HTTP logs, Wikimedia pageviews, or Azure Functions trace — verify availability and licence, record access date and URL), time-compressed. Report simple similarity statistics (distribution shape, autocorrelation, burstiness index). If a synthetic pattern is not realistic, say so.
8. **Workload predictability must be quantified, not just labelled** (needed for RQ1/H1): compute per-pattern a forecastability measure (e.g. spectral-entropy-based forecastability, autocorrelation at the dominant lag, and seasonal-naive MASE) from the history traces, and use it as the x-axis in the analysis.
9. **Statistics:** report median and IQR per cell, bootstrap 95 % CIs for the A−B differences, exact Mann–Whitney U / permutation tests (independent runs) with effect size (Cliff's δ or Hodges–Lehmann), and multiple-comparison handling (Holm). With only 5 runs per cell, CIs are wide and per-pattern tests are exploratory: lead with effect sizes and the full distributions (show every run as a point), and say so in the paper. For H2/H3 associations use Spearman correlation at run level, plus a pattern-clustered bootstrap, and say plainly that runs within a pattern are not independent samples of "workload types". No causal language.
10. **Pilot before campaign:** run 1–2 repetitions of every pattern/policy first. Confirm the 300 s window contains at least 2 scale-out events per run on the bursty and gradual patterns, and that periodic/burst cycles are short enough for the window to hold 4 or more cycles. If not, **stop and report**; do not quietly change the window.
11. **Keep the cluster honest:** record host spec (CPU/RAM/OS), Docker/kind versions, node count, and note that results are from a single-host local cluster (limitation).
12. **Scope: scale-up behaviour.** HPA's default scale-down stabilisation window is 300 s, so scale-down will mostly not appear inside the 300 s window. Use the **identical** scale-down behaviour setting in both arms, document it, and describe over-provisioning as "extra capacity held after demand", not a full scale-up/scale-down cycle. Do not claim anything about scale-down efficiency.

**Run budget (plan with this):** pilot ~10 runs; dev tuning ~20-30 runs (dev seeds only); **final block 1 = 5 patterns x 2 arms x 5 reps = 50 runs (about 7 h at ~8 min per run including reset/warm-up)**; optional block 2 = another 50 runs; negative tests ~15 runs. Measure the real per-run overhead in the pilot and report it.

## 5. Capstone mapping (what must exist by the end)

| Spec item | Where it lives |
|---|---|
| O1 baseline HPA + load + traces; problem reproduced with versioned evidence | `docs/O1_problem_validation.md`, `results/raw/o1/` |
| O2 ≥ 2 forecasters compared (Prophet vs LSTM, plus a seasonal-naive baseline) with documented selection trade-off (accuracy, fit time, data need, resource use) | `forecasting/`, `docs/O2_reference.md` |
| O3 KEDA + forecast scaler, separately testable, versioned config, ablation/sensitivity (e.g. refit cadence or history window, small) | `scaler/`, `docs/O3_contribution.md` |
| O4 integrated system, one-command demo + full reproduce | `Makefile` (`make demo`, `make reproduce`), `docs/OPERATING_GUIDE.md` |
| O5 acceptance: AC-1…AC-4, KPI-1…KPI-6, NT1…NT5, validation report | `acceptance/`, `docs/ACCEPTANCE_REPORT.md` |
| Evidence manifest (source URL, access date, licence, seed/fixture IDs, transformations, config, which KPI uses it) | `docs/EVIDENCE_MANIFEST.md` |

**Negative tests (implement as scripted fault injection with fixture IDs, expected vs observed, recovery time):**
- NT1 forecast errors → under-provisioning (inject biased/lagged forecast); NT2 scaling lag and cold starts (slow-readiness app variant / larger startup delay); NT3 synthetic load not resembling real patterns (validation from rule 7); NT4 control-plane/dependency outage (kill forecaster pod, Prometheus, or KEDA operator) → bounded recovery; NT5 configuration drift / unsafe rollout (manual edit of ScaledObject/HPA; a drift checker must detect it and reconcile).
- **Degraded mode:** when the forecaster is stale/unavailable, the system must fall back to a reactive trigger (e.g. KEDA multi-trigger or fallback config) with no silent state loss. This fallback config is tested only in the NT tests; the main experiment keeps the forecast-only Arm B.

## 6. Target application and architecture (suggested; deviate only with a reason)

- Local cluster: kind or k3d, ≥ 2 worker nodes if the machine allows; metrics-server; Prometheus (kube-prometheus-stack or a minimal Prometheus); KEDA (latest stable at time of work, pinned); Grafana optional for the demo.
- Test service: small FastAPI (or Go) HTTP app with tunable CPU-bound work per request, `/healthz`, readiness probe, Prometheus metrics (`http_requests_total`, latency histogram), fixed startup delay documented. Fixed resource requests/limits.
- Forecaster service: Prophet (+ LSTM variant for O2 comparison offline), reads request-rate history from Prometheus, refits on a fixed cadence, publishes `forecast_request_rate_60s` (and forecast timestamps/origin) as a Prometheus gauge; exposes health + staleness metric.
- KEDA ScaledObject (Prometheus scaler) on the forecast gauge; desired replicas = ceil(forecast / calibrated per-pod target).
- Experiment runner (Python): sets up a run, records `t0` and onset timestamps, starts k6, watches pod events via the Kubernetes API, scrapes Prometheus, writes one directory per run with raw CSV/JSON + config snapshot + git commit hash.
- Analysis pipeline (Python, pandas/scipy/matplotlib): raw → tables/figures only through scripts, `make analyze`.

## 7. Repository layout

```
app/  workloads/  forecasting/  scaler/  k8s/  runner/  analysis/  acceptance/
results/{raw,invalid,processed}/   figures/   docs/   paper/
Makefile  README.md  requirements.lock  PLAN.md  CHANGELOG.md
```

## 8. Working protocol

- Work in phases. At the end of each phase: (1) list what was built, (2) show the evidence (command outputs, file paths, small raw-data excerpts), (3) list open problems/risks honestly, (4) **stop and wait for my approval.**
- Commit small and often. Every experiment run stores the git commit hash it ran on.
- **Git/GitHub at every phase:** before you stop at a gate, make sure everything is committed and pushed to GitHub with plain commands: `git add .`, `git commit -m "<message>"`, `git push`. The commit message must say, in a few words, what was actually done in that phase or step (for example: "Add kind cluster, test app and baseline HPA", "Add workload generators and Prophet vs LSTM comparison"), never generic text like "Finish phase 1". Also push after any large chunk of work inside a phase, and after the campaign finishes. Do not use tags. Never force-push or rewrite history. Never commit secrets, tokens or credentials. Check file sizes before committing raw results: if `results/raw/` gets large (over roughly 50 MB), compress it or use Git LFS, tell me, and keep a manifest with checksums. If a push fails (auth, network), tell me instead of working around it.
- Write tests for the forecaster, the metric computations (use synthetic fixtures with known answers, an "independent oracle"), and the config validator.
- Keep a running `CHANGELOG.md` and a `docs/DECISIONS.md` (ADR-style: decision, alternatives, reason).
- Explain tradeoffs in plain language; I'm a student and need to defend this in a viva.

---

# PART 2 — PHASE PROMPTS (paste one at a time)

## P0 — Environment audit and plan  *(Opus 4.6)*
```
Read PROJECT_BRIEF.md fully, then the spec PDF and the paper draft in docs/.
1) Summarise the locked design in 10 lines and list anything in the brief/spec/draft that is ambiguous, inconsistent, or risky. Put spec inconsistencies in docs/SPEC_NOTES.md.
2) Audit my machine: OS, CPU cores, RAM, free disk, whether Docker, kubectl, kind/k3d, helm, python, k6 are installed (do not install yet). Tell me what cluster size is realistic and the maximum replicas that will fit without Pending pods.
3) Estimate total campaign time for the plan 5 patterns x 2 arms (HPA, Prophet+KEDA) x 5 repetitions = 50 final runs (plus the optional second block of 50), including warm-up, the 300 s scored window, cool-down and reset. Tell me the realistic per-run time on my machine and whether the plan fits in about 2-3 days of machine time. Do not change the number of arms or repetitions; tell me if it does not fit.
4) Write PLAN.md: phases, deliverables, pinned-version candidates (look up the current stable versions of Kubernetes node image, KEDA, Prometheus stack, Prophet, k6), risks, and a repo scaffold.
Stop and wait for approval.
```

## P1 — O1: cluster, app, load generator, baseline HPA, calibration  *(Sonnet 4.6)*
```
Implement Phase O1 per PROJECT_BRIEF.md.
- Create the local cluster, install metrics-server, Prometheus, and KEDA (pinned, scripted in Makefile).
- Build the test service with tunable CPU work, readiness probe, Prometheus metrics; build and preload the image into the cluster.
- Write the k6 open-loop load scripts (arrival-rate executors) and a runner skeleton that records t0, load-onset timestamps, offered vs achieved rate, pod events (creation and Ready timestamps), Prometheus series, and a config snapshot + git hash per run.
- Deploy the default-style HPA (CPU) and reproduce the reactive-lag problem with a step load: show replica count vs offered load vs latency over time, with raw data in results/raw/o1/.
- Calibrate per-pod sustainable capacity (rps at which latency stays within limits) and derive the SLO from steady-state baseline runs; write docs/SLO.md and docs/METRICS.md and FREEZE them (commit and push; do not edit afterwards).
- Unit-test the metric functions (D_E2E, D_provision, D_detect+sched, onset-relative lag, overprovisioned pod-hours, cost per successful request) against synthetic fixtures with hand-computed answers.
Gate 1 evidence: plot + raw files for the baseline, tests passing, frozen SLO. Stop and wait.
```

## P2 — Workloads, predictability, and O2 forecaster comparison  *(Opus 4.6 for design, Sonnet for code)*
```
Implement the five workload generators (stable, periodic, gradually changing, bursty, noisy) with explicit parameters and seeds in workloads/SPEC.md. Generate: (a) a separate warm-up/history trace per pattern and seed, (b) dev seeds and acceptance seeds (generate and hash the acceptance seeds now; do not look at results from them).
Compute a quantitative predictability/forecastability measure per pattern (see brief rule 8).
Validate synthetic realism against at least one public real trace (brief rule 7); record URL, access date, licence.
O2: on the history traces, compare Prophet (with custom short-period seasonality, defaults disabled), an LSTM, and a seasonal-naive baseline for a 60 s horizon. Report MAE, RMSE, sMAPE/MASE, fit time, inference time, memory, and data requirement. Write docs/O2_reference.md with a documented selection trade-off. The paper uses Prophet; if another model is clearly better, report that honestly and explain why Prophet is still used (or tell me so we can discuss).
Add tests with an independent oracle (known-signal fixtures with known forecast error).
Gate 2 evidence + stop.
```

## P3 — O3: forecaster service + KEDA predictive scaler  *(Sonnet 4.6)*
```
Build the forecaster as a containerised service that reads request-rate history from Prometheus, refits on a fixed cadence, and exposes forecast_request_rate_60s plus forecast origin/target timestamps, forecast latency, and a staleness metric. Build the KEDA ScaledObject (Prometheus scaler) for Arm B. Make Arms A and B share the same HPA behavior policy (including the same scale-down setting), min/max replicas and polling interval, and write a config-diff table to docs/FAIRNESS.md. Add a config validator (script) that refuses to start a run if arms differ in anything other than the intended factor.
Demonstrate a vertical slice: one bursty run per arm, with a figure of offered load, forecast, replicas (desired vs ready), and latency on one time axis. Include a small ablation/sensitivity (e.g. refit cadence or history window) on DEV seeds only.
Evidence + stop.
```

## P4 — O4: experiment campaign  *(Sonnet 4.6, then you run it)*
```
Finish the resumable, append-only campaign runner (randomised/interleaved order, precondition checks, automatic invalidation with reason, per-run directories). Run the PILOT (1-2 reps per pattern x arm) and report: do runs behave sanely? does the 300 s window contain at least 2 scale-out events on bursty and gradual patterns? are offered/achieved rates matched? Report the real per-run overhead (reset, warm-up, cool-down). Do NOT change the 300 s window or the 60 s horizon; report and wait if there is a problem.
After my approval: write docs/PROTOCOL_FROZEN.md (all parameters, seeds, commit hash, acceptance-seed hash), commit and push it, and give me the exact command to launch the full campaign in my own terminal (dev tuning first on dev seeds, then block 1: 50 runs on the first 5 acceptance seeds, exactly once; block 2 only on my explicit go). Provide a progress/status command and a resume command.
```

## P5 — O5: negative tests, degraded mode, recovery  *(Sonnet 4.6)*
```
Implement NT1-NT5 as scripted fault injections with fixture IDs, preconditions, expected vs observed behaviour, recovery time, and residual-risk notes (brief Section 5). Implement and test the degraded-mode fallback (forecaster stale/unavailable -> reactive trigger) and the drift checker for NT5. Store evidence under results/raw/nt/ and write acceptance/negative_tests.md from the data. Be honest about any test where the system does not behave safely; that is a finding, not a failure to hide.
```

## P6 — Analysis, KPIs, acceptance  *(Opus 4.6)*
```
Build the analysis pipeline (make analyze) from raw results to processed tables and figures only through scripts. Produce:
- KPI-1...KPI-6 table: baseline (Arm A, HPA) vs candidate (Arm B, Prophet+KEDA), per pattern, median/IQR, deltas with bootstrap 95% CI, Mann-Whitney p with Holm correction, effect size; pass/fail against the spec rules; list spec ambiguities again (SPEC_NOTES.md).
- RQ1: benefit vs workload pattern, plotted against the quantitative predictability measure.
- RQ2/H2: forecast error (MAE, RMSE, sMAPE/MASE) vs outcome differences (P99, SLO violations, over/under-provisioning); run-level Spearman + clustered bootstrap.
- RQ3/H3: D_provision, D_detect+sched, D_E2E, onset-relative lead/lag, lead-time slack (60 s - D_provision) vs benefit.
- Required figures: per-pattern time-aligned plot (offered load, forecast, desired/ready replicas, P95/P99), latency boxplots, error-vs-benefit scatter, slack-vs-benefit scatter. 300 dpi PNG and PDF.
- docs/ACCEPTANCE_REPORT.md covering AC-1...AC-4, all KPIs, all NTs, limitations, residual risks.
Describe what the data shows in plain words, including where predictive scaling did NOT help. No claim of causation. Flag anything surprising or suspicious in the data and check it (e.g. outliers, invalid runs, clock issues) before interpreting.
```

## P7 — Paper sections, reproducibility package, mentor demo  *(Opus 4.6)*
```
1) Paper: write Section V (Experimental Setup: cluster, versions, application, workload table, parameters, fairness config, SLO definition, repetitions, statistics), Section VI (Results, RQ1-RQ3, H1-H3 stated as supported / not supported / partly supported by the data), Section VII (Discussion), Section VIII (Threats to validity and limitations: single-host cluster, 1 s timestamp resolution, synthetic workloads, fixed 60 s horizon and 300 s window, one forecaster in the main study, HPA-vs-KEDA differing in scaling metric and controller path as well as in forecasting, 5 repetitions per condition, scale-up behaviour only, etc.), Section IX (Conclusion). Match the existing draft's IEEE style and equation numbering. Every number must come from results/processed/*.csv; add a comment next to each number with its source file. Do not invent references: if you need more citations, list them as "TODO: verify" for me to look up. Also propose a small addition to Section IV-F describing the onset-relative metric and lead-time slack.
2) Reproducibility: make reproduce (clean environment, pinned versions, seeds, scripted runbook), evidence manifest, operating guide, README with a quick-start.
3) Mentor demo: make demo runs a short (about 10-12 minutes) live comparison of Arm A vs Arm B on the bursty and periodic patterns with a Grafana dashboard (or live terminal view) showing offered load, forecast, replicas, latency; then prints the KPI summary from the stored full-campaign data. Write a 5-minute demo script and a viva Q&A sheet (likely questions and honest answers, including limitations).
4) Final self-audit: list every claim in the paper, its source file, and any claim that is weaker than the data supports or stronger than it. Fix before I submit.
```