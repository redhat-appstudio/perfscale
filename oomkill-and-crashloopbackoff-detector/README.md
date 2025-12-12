# OOMKilled / CrashLoopBackOff detector (oc_get_ooms.py)

A high-performance, parallel OOMKilled / CrashLoopBackOff detector for OpenShift & Kubernetes clusters using `oc`, with optional Prometheus fallback, rich exports, and forensic artifact collection.

---

## 🚀 What This Tool Does

- Scans **one or many OpenShift clusters** (`oc` contexts)
- Detects:
  - **OOMKilled pods**
  - **CrashLoopBackOff pods**
- Looks back across **multiple time windows**:
  - 1h, 3h, 6h, 24h, 48h, 3d, 5d, 7d
- Uses:
  - Kubernetes **events** first (fast)
  - **Prometheus fallback** for older history
- Runs **highly parallel**:
  - Cluster-level batching
  - Namespace-level batching
- Saves **forensic artifacts**:
  - `oc describe pod`
  - `oc logs` (or `--previous`)
- Exports **CSV + JSON** with absolute paths to artifacts
- Colorized terminal output

---

## 🧠 Architecture Overview

```text
                         ┌────────────────────────┐
                         │  oc config get-contexts│
                         └────────────┬───────────┘
                                      │
                       Context batching (N clusters)
                                      │
          ┌───────────────────────────┴─────────────────────────────┐
          │                                                         │
┌─────────▼─────────┐                                     ┌─────────▼─────────┐
│  Cluster Worker   │                                     │  Cluster Worker   │
│ (context A)       │                                     │ (context B)       │
└─────────┬─────────┘                                     └─────────┬─────────┘
          │                                                         │
  Fetch namespaces                                            Fetch namespaces
          │                                                         │
 Namespace batching (10 default)                             Namespace batching
          │                                                         │
┌─────────▼─────────┐                                     ┌─────────▼─────────┐
│ Namespace Workers │  (parallel)                         │ Namespace Workers │
│  oc get events    │                                     │  oc get events    │
│  detect OOM / CLB │                                     │  detect OOM / CLB │
└─────────┬─────────┘                                     └─────────┬─────────┘
          │                                                         │
 If older data needed                                   If older data needed
          │                                                         │
┌─────────▼─────────┐                                     ┌─────────▼─────────┐
│Prometheus Fallback│  (batched + parallel)               │Prometheus Fallback│
└─────────┬─────────┘                                     └─────────┬─────────┘
          │                                                         │
 Save artifacts:                                              Save artifacts:
 - pod describe                                               - pod describe
 - pod logs / previous                                        - pod logs / previous
          │                                                         │
┌─────────▼─────────┐                                     ┌─────────▼─────────┐
│ CSV / JSON Export │                                     │ CSV / JSON Export │
└───────────────────┘                                     └───────────────────┘
```

---

## ⚙️ Parallelism Model

| Layer            | Default | Controlled By |
|------------------|---------|---------------|
| Cluster batching | 2       | `--batch-size` |
| Namespace batch  | 10      | `--ns-batch-size` |
| Namespace workers| 5       | `--ns-workers` |
| Prometheus batch | Same as namespace batch | `--ns-batch-size` |

Prometheus fallback is **bounded and safe** for large clusters.

---

## 📂 Artifact Storage Layout

Artifacts are stored **per cluster**:

```
/tmp/<cluster_name>/
  <namespace>__<pod>__<timestamp>__desc.txt
  <namespace>__<pod>__<timestamp>__log.txt
```

Example:

```
/tmp/kflux-prd-es01/
  clusters-a53fda0e...__catalog-operator__2025-12-12T05-25-40Z__desc.txt
  clusters-a53fda0e...__catalog-operator__2025-12-12T05-25-40Z__log.txt
```

If `oc logs` returns no data, the tool automatically retries with:

```
oc logs --previous
```

---

## 📤 Output Formats

### CSV Columns

```
cluster,
namespace,
pod,
type,
timestamps,
sources,
description_file,
pod_log_file
```

### JSON Structure (simplified)

```json
{
  "cluster": "kflux-prd-es01",
  "namespace": "clusters-a53fda0e...",
  "pod": "catalog-operator-79c5668759-hfrq8",
  "type": "CrashLoopBackOff",
  "timestamps": [
    "2025-12-12T05:25:40Z"
  ],
  "sources": ["events"],
  "artifacts": {
    "description_file": "/tmp/kflux-prd-es01/...__desc.txt",
    "pod_log_file": "/tmp/kflux-prd-es01/...__log.txt"
  }
}
```

---

## 🧪 Example Runs

### Run on current context only

```bash
./oc_get_ooms.py --current
```

### Run on specific contexts

```bash
./oc_get_ooms.py \
  --contexts default/api-stone-prd-rh01...,default/api-kflux-prd...
```

### Run on all contexts (default)

```bash
./oc_get_ooms.py
```

### High-performance mode for very large clusters

```bash
./oc_get_ooms.py \
  --batch-size 3 \
  --ns-batch-size 20 \
  --ns-workers 10
```

### Skip Prometheus fallback

```bash
./oc_get_ooms.py --skip-prometheus
```

### Namespace filtering (regex)

Only namespaces containing `tenant`, exclude `test`:

```bash
./oc_get_ooms.py \
  --include-ns tenant \
  --exclude-ns test
```

Multiple regex patterns:

```bash
./oc_get_ooms.py \
  --include-ns "tenant|prod" \
  --exclude-ns "debug|sandbox"
```

---

## 🎨 Terminal Output

- **Green** → no issues
- **Yellow** → namespace scanned
- **Red** → OOM / CrashLoopBackOff detected
- **Cyan** → cluster boundaries
- **Gray** → skipped or unreachable clusters

---

## 🛡️ Resilience & Safety

- Retries on TLS / API failures
- Configurable timeouts
- Graceful skipping of unreachable clusters
- Prometheus rate-safe batching
- Namespaces printed **only if issues are found**

---

## 📌 Requirements

- Python **3.9+**
- `oc` CLI in PATH
- Logged in (`oc whoami` must succeed)
- Prometheus access (optional)

---

## 📄 Files Generated

| File | Purpose |
|------|---------|
| `oom_results.csv` | Spreadsheet-friendly output |
| `oom_results.json` | Structured automation input |
| `/tmp/<cluster>/*.txt` | Pod forensic artifacts |

---

## 🧠 Design Philosophy

> **Fast, safe, forensic-grade, and cluster-scale.**

---

## 📝 License

Internal / Team Utility  
Adapt as needed.

---

## 🔮 Future Enhancements & Roadmap

The following enhancements are **intentionally planned** and align with the current architecture.
Most can be added incrementally without redesigning the tool.

---

### 📈 1. OOM / CrashLoopBackOff Trend Analysis

Analyze **patterns over time** across:

- Namespaces
- Clusters
- Workloads
- Time windows

Examples:

- Which namespaces OOM most frequently?
- Which clusters are most unstable?
- Are OOMs increasing week-over-week?
- Which pods repeatedly crash after restarts?

#### Possible Outputs

```text
Cluster          Namespace        OOMs(7d)  CLB(7d)  Trend
----------------------------------------------------------
kflux-prd-es01   tenant-a         24        3        ↑↑
kflux-prd-es01   tenant-b         2         15       ↑
stone-prd-rh01   tenant-x         0         8        →
```

This can be implemented by:
- Persisting JSON outputs across runs
- Aggregating by `(cluster, namespace, pod)`
- Applying rolling time windows (7d / 30d)

---

### 📊 2. Namespace Stability Scoring

Compute a **stability score** per namespace:

```text
score = f(OOM count, CrashLoop count, restart frequency)
```

Example:

```text
Namespace        Score   Status
--------------------------------
tenant-prod-a    92      Stable
tenant-prod-b    61      Warning
tenant-prod-c    28      Critical
```

This allows:
- Ranking tenants
- Capacity planning
- SLO enforcement

---

### 🧠 3. Memory Pressure Correlation

Correlate OOMs with:
- Container memory limits
- Actual memory usage (Prometheus)
- Node memory pressure

Answer questions like:
- Are OOMs caused by under-sized limits?
- Are multiple namespaces competing on the same nodes?
- Do OOMs align with traffic spikes?

---

### 📉 4. Historical Baseline & Regression Detection

Automatically detect regressions:

- “Namespace X normally has 0–1 OOMs/week, now has 12”
- “CrashLoopBackOff appeared after deployment Y”

This could integrate with:
- Deployment timestamps
- Image changes
- ConfigMap updates

---

### 🧾 5. Persistent Storage Backend

Optional persistence layer:

- SQLite (local)
- PostgreSQL
- S3 / Object Storage

Use cases:
- Long-term trend analysis
- Dashboards
- Audit trails

---

### 📊 6. HTML / Web Report Generation

Generate:
- Static HTML reports
- Per-cluster dashboards
- Per-namespace drilldowns

Example command:

```bash
./oc_get_ooms.py --html-report out.html
```

---

### 📡 7. Alerting & Integrations

Integrations could include:

- Slack
- Email
- PagerDuty
- Jira / ServiceNow
- GitHub Issues

Example:
```text
ALERT: tenant-prod-x had 5 OOMs in last 6h on cluster kflux-prd-es01
```

---

### 📍 8. Grafana Annotations

Automatically annotate Grafana dashboards when:
- OOMs occur
- CrashLoopBackOff starts
- Thresholds are exceeded

This links incidents directly to metrics timelines.

---

### 🧪 9. Canary / Deployment Awareness

Enhance detection by:
- Linking OOMs to recent rollouts
- Identifying bad canary deployments
- Comparing old vs new ReplicaSets

---

### 🧵 10. Event Deduplication & Root Cause Grouping

Group related failures:

- Same pod template
- Same container
- Same error signature

Example:

```text
Root Cause: insufficient memory limit (256Mi)
Affected Pods: 17
Affected Namespaces: 4
```

---

### 🔐 11. RBAC & Least-Privilege Mode

Add flags for:
- Namespace-scoped scanning
- Read-only operation
- Limited artifact collection

Useful for:
- Tenant self-service diagnostics
- Restricted environments

---

### 🧩 12. Plugin Architecture

Enable pluggable detectors:

```text
detectors/
  oom.py
  crashloop.py
  diskpressure.py
  cpuhog.py
```

Allow teams to add custom failure modes without modifying core logic.

---

## 🧠 Long-Term Vision

> Move from **reactive troubleshooting** → **predictive reliability insights**

This tool can evolve into:
- A fleet-wide reliability scanner
- A capacity planning assistant
- An SRE forensic toolkit

---


