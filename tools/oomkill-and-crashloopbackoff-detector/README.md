# OOMKilled / CrashLoopBackOff detector (oc_get_ooms.py)

A high-performance, parallel OOMKilled / CrashLoopBackOff detector for OpenShift & Kubernetes clusters using `oc`, with optional Prometheus fallback, rich exports, and forensic artifact collection.

---

## 🚀 What This Tool Does

- Scans **one or many OpenShift clusters** (`oc` contexts)
- Detects:
  - **OOMKilled pods** (via events, pod status, and Prometheus)
  - **CrashLoopBackOff pods** (via events, pod status, and Prometheus)
- Configurable **time range filtering** (default: 1 day)
  - Format: `1h`, `6h`, `1d`, `7d`, `1M` (30 days), etc.
- Uses multiple detection methods:
  - Kubernetes **events** (optimized: single API call per namespace)
  - **Pod status** (direct check for OOMKilled/CrashLoopBackOff)
  - **Prometheus fallback** via route-based HTTP access (no exec permissions needed)
- Runs **highly parallel** with constant parallelism:
  - Cluster-level parallelism (maintains constant workers)
  - Namespace-level batching
  - Automatic load balancing across clusters
- Saves **forensic artifacts**:
  - `oc describe pod`
  - `oc logs` (or `--previous`)
- Exports **CSV + JSON** with absolute paths to artifacts and time range metadata
- Colorized terminal output

---

## 🧠 Architecture Overview

```text
                         ┌────────────────────────┐
                         │  oc config get-contexts│
                         └────────────┬───────────┘
                                      │
                    Constant Parallelism Pool (N workers)
                    (When one finishes, next starts immediately)
                                      │
          ┌───────────────────────────┴─────────────────────────────┐
          │                                                         │
┌─────────▼─────────┐                                     ┌─────────▼─────────┐
│  Cluster Worker   │                                     │  Cluster Worker   │
│ (context A)       │                                     │ (context B)       │
│                   │                                     │                   │
│  [Processing...]  │                                     │  [Processing...]  │
│                   │                                     │                   │
└─────────┬─────────┘                                     └─────────┬─────────┘
          │                                                         │
          │ When A finishes → Start Cluster C                       │
          │ When B finishes → Start Cluster D                       │
          │ (Maintains constant parallelism)                        │
          │                                                         │
          │ Fetch namespaces (with time range filter)               │
          │                                                         │
          │ Namespace batching (10 default)                         │
          │                                                         │
┌─────────▼─────────┐                                     ┌─────────▼─────────┐
│ Namespace Workers │  (parallel)                         │ Namespace Workers │
│  ┌──────────────┐ │                                     │  ┌──────────────┐ │
│  │Single API    │ │                                     │  │Single API    │ │
│  │call: events  │ │                                     │  │call: events  │ │
│  └──────────────┘ │                                     │  └──────────────┘ │
│  ┌──────────────┐ │                                     │  ┌──────────────┐ │
│  │Pod status    │ │                                     │  │Pod status    │ │
│  │check: OOM/CLB│ │                                     │  │check: OOM/CLB│ │
│  └──────────────┘ │                                     │  └──────────────┘ │
└─────────┬─────────┘                                     └─────────┬─────────┘
          │                                                         │
          │ If needed: Prometheus fallback                          │
          │ (via route-based HTTP, no exec perms)                   │
          │                                                         │
┌─────────▼─────────┐                                     ┌─────────▼─────────┐
│Prometheus Fallback│  (batched + parallel)               │Prometheus Fallback│
│(Route-based HTTP) │                                     │(Route-based HTTP) │
└─────────┬─────────┘                                     └─────────┬─────────┘
          │                                                         │
 Save artifacts:                                              Save artifacts:
 - pod describe                                               - pod describe
 - pod logs / previous                                        - pod logs / previous
          │                                                         │
┌─────────▼─────────┐                                     ┌─────────▼─────────┐
│ CSV / JSON Export │                                     │ CSV / JSON Export │
│ (with time_range) │                                     │ (with time_range) │
└───────────────────┘                                     └───────────────────┘
```

---

## ⚙️ Parallelism Model

| Layer            | Default | Controlled By | Notes |
|------------------|---------|---------------|-------|
| Cluster parallelism | 2       | `--batch` | **Constant parallelism**: When one cluster finishes, immediately starts the next one |
| Namespace batch  | 10      | `--ns-batch-size` | Number of namespaces processed per batch |
| Namespace workers| 5       | `--ns-workers` | Thread pool size for namespace processing |
| Prometheus batch | Same as namespace batch | `--ns-batch-size` | Prometheus queries batched for rate safety |

**Key Improvements:**
- **Constant Parallelism**: Cluster processing maintains `--batch N` workers throughout execution. When one cluster completes, the next one starts immediately (no waiting for entire batch).
- **Optimized Events**: Single API call per namespace fetches all events, then filters in-memory (3x faster than previous approach).
- **Multiple Detection Methods**: Checks events, pod status, and Prometheus for comprehensive coverage.

Prometheus fallback is **bounded and safe** for large clusters and uses route-based HTTP access (no exec permissions required).

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
pod_log_file,
time_range
```

**Type values:**
- `OOMKilled` - Pod was killed due to out-of-memory
- `CrashLoopBackOff` - Pod is in crash loop state

**Sources:**
- `events` - Found via Kubernetes events
- `oc_get_pods` - Found via direct pod status check
- `prometheus` - Found via Prometheus metrics

**Time Range:**
- Shows the time range used for detection (e.g., `1d`, `6h`, `1M`)

### JSON Structure

```json
{
  "_metadata": {
    "time_range": "1d"
  },
  "kflux-prd-es01": {
    "clusters-a53fda0e...": {
      "catalog-operator-79c5668759-hfrq8": {
        "pod": "catalog-operator-79c5668759-hfrq8",
        "oom_timestamps": [],
        "crash_timestamps": [
          "2025-12-12T05:25:40Z"
        ],
        "sources": ["events"],
        "description_file": "/tmp/kflux-prd-es01/...__desc.txt",
        "pod_log_file": "/tmp/kflux-prd-es01/...__log.txt"
      }
    }
  }
}
```

**Structure:**
- `_metadata.time_range` - Time range used for detection
- `cluster` → `namespace` → `pod` → pod details
- `oom_timestamps` - Array of OOMKilled event timestamps
- `crash_timestamps` - Array of CrashLoopBackOff event timestamps
- `sources` - Array of detection methods used

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
  --batch 4 \
  --ns-batch-size 250 \
  --ns-workers 250 \
  --timeout 200
```

**Note:** `--batch` maintains constant parallelism. With `--batch 4`, the tool always processes 4 clusters simultaneously. When one finishes, the next one starts immediately.

### Time range filtering

Filter events by time range (default: 1 day):

```bash
# Last 1 hour
./oc_get_ooms.py --time-range 1h

# Last 6 hours
./oc_get_ooms.py --time-range 6h

# Last 7 days
./oc_get_ooms.py --time-range 7d

# Last 1 month (30 days)
./oc_get_ooms.py --time-range 1M
```

**Time range formats:**
- `s` = seconds
- `m` = minutes
- `h` = hours
- `d` = days
- `M` = months (30 days)

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
- **Route-based Prometheus access** (no exec permissions required)
- **Time range filtering** to focus on recent events
- **Multiple detection methods** for comprehensive coverage:
  - Kubernetes events (optimized single API call)
  - Direct pod status checks
  - Prometheus metrics (fallback)
- Namespaces printed **only if issues are found**

---

## 📌 Requirements

- Python **3.9+**
- `oc` CLI in PATH
- Logged in (`oc whoami` must succeed)
- `requests` library (for Prometheus access): `pip install requests`
- Prometheus route access (optional, for fallback detection)
  - No exec permissions needed - uses route-based HTTP access
  - Requires route read access in `openshift-monitoring` namespace

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

**Recent Enhancements:**
- **Constant Parallelism**: Maintains optimal resource utilization across all clusters
- **Performance Optimized**: Single event API call per namespace (3x faster)
- **Comprehensive Detection**: Multiple methods ensure no OOM/CrashLoop pods are missed
- **Time Range Filtering**: Focus on recent events with configurable lookback window
- **Permission-Friendly**: Prometheus access via routes (no exec permissions needed)
- **Consistent Output**: CSV and JSON formats are synchronized and include metadata

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


