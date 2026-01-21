# OOMKilled / CrashLoopBackOff detector (oc_get_ooms.py)

A high-performance, parallel OOMKilled / CrashLoopBackOff detector for OpenShift & Kubernetes clusters using `oc`, with rich exports and forensic artifact collection.

---

## 🚀 What This Tool Does

- Scans **one or many OpenShift clusters** (`oc` contexts)
- Detects:
  - **OOMKilled pods** (via events and pod status)
  - **CrashLoopBackOff pods** (via events and pod status)
- Configurable **time range filtering** (default: 1 day)
  - Format: `1h`, `6h`, `1d`, `7d`, `1M` (30 days), etc.
- Uses multiple detection methods:
  - Kubernetes **events** (optimized: single API call per namespace)
  - **Pod status** (direct check for OOMKilled/CrashLoopBackOff)
- Runs **highly parallel** with constant parallelism:
  - Cluster-level parallelism (maintains constant workers)
  - Namespace-level batching
  - Automatic load balancing across clusters
- Saves **forensic artifacts**:
  - `oc describe pod`
  - `oc logs` (or `--previous`)
- Exports **multiple formats** with absolute paths to artifacts and time range metadata:
  - **CSV** - Spreadsheet-friendly format
  - **JSON** - Structured automation input
  - **HTML** - Standalone visual report (open in browser)
  - **TABLE** - Human-readable text table
- **Automatic ephemeral namespace exclusion** on EaaS clusters
  - Excludes ephemeral test and cluster namespaces by default to avoid false positives
  - Ephemeral cluster namespaces: `clusters-<uuid>` pattern
  - Ephemeral test namespaces: `test-*`, `e2e-*`, `ephemeral-*`, `ci-*`, etc.
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
 Save artifacts:                                              Save artifacts:
 - pod describe                                               - pod describe
 - pod logs / previous                                        - pod logs / previous
          │                                                         │
┌─────────▼─────────┐                                     ┌─────────▼─────────┐
│ Multi-Format Export│                                     │ Multi-Format Export│
│ CSV/JSON/HTML/TABLE│                                     │ CSV/JSON/HTML/TABLE│
│ (with time_range)  │                                     │ (with time_range)  │
└───────────────────┘                                     └───────────────────┘
```

---

## ⚙️ Parallelism Model

| Layer            | Default | Controlled By | Notes |
|------------------|---------|---------------|-------|
| Cluster parallelism | 2       | `--batch` | **Constant parallelism**: When one cluster finishes, immediately starts the next one |
| Namespace batch  | 10      | `--ns-batch-size` | Number of namespaces processed per batch |
| Namespace workers| 5       | `--ns-workers` | Thread pool size for namespace processing |

**Key Improvements:**
- **Constant Parallelism**: Cluster processing maintains `--batch N` workers throughout execution. When one cluster completes, the next one starts immediately (no waiting for entire batch).
- **Optimized Events**: Single API call per namespace fetches all events, then filters in-memory (3x faster than previous approach).
- **Multiple Detection Methods**: Checks events and pod status for comprehensive coverage.

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

The tool automatically generates **four output formats** for maximum flexibility:

### 1. CSV (`oom_results.csv`)

Spreadsheet-friendly format with all findings in tabular form.

**Columns:**
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

**Time Range:**
- Shows the time range used for detection (e.g., `1d`, `6h`, `1M`)

### 2. JSON (`oom_results.json`)

Structured format perfect for automation, scripting, and integration with other tools.

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

### 3. HTML (`oom_results.html`)

**Standalone visual report** that can be opened directly in any web browser. Perfect for:
- Sharing findings with team members
- Quick visual overview
- Presentation-ready reports
- Clickable links to artifact files

**Features:**
- **Summary statistics** - Total findings, OOM vs CrashLoopBackOff counts
- **Cluster-level grouping** - Organized by cluster for easy navigation
- **Namespace drilldown** - Expandable namespace sections
- **Color-coded badges** - Visual indicators for issue types
- **Clickable artifact links** - Direct links to description and log files
- **Responsive design** - Works on desktop and mobile devices
- **No external dependencies** - Fully self-contained HTML file

Simply **double-click** `oom_results.html` in Finder (macOS) or File Explorer (Windows/Linux) to open it in your default browser.

### 4. TABLE (`oom_results.table`)

Human-readable text table format, perfect for terminal viewing or plain text reports.

---

## 🧪 Example Runs

### Getting Help

To see all available options and usage information:

```bash
./oc_get_ooms.py --help
# or
./oc_get_ooms.py -h
```

### Basic Usage

#### Run on current context only
```bash
./oc_get_ooms.py --current
```

#### Run on all contexts (default)
```bash
./oc_get_ooms.py
```

#### Run on specific contexts using substrings
You can use **substrings** to match contexts (the script will automatically find the full context names):

```bash
./oc_get_ooms.py --contexts kflux-prd-rh02,stone-prd-rh01
```

The script will:
- Retrieve all available contexts from `oc config get-contexts` (or `kubectl` as fallback)
- Match each substring against available contexts (case-insensitive)
- Show confirmation messages: `Matched 'kflux-prd-rh02' -> 'default/api-stone-prod-p02-hjvn-p1-openshiftapps-com:6443/smodak'`
- Exit with an error if a substring matches multiple contexts or no contexts (with helpful suggestions)

**Note:** You can still use full context strings if preferred, but substrings are much more convenient.

### Performance Tuning

#### High-performance mode for very large clusters
```bash
./oc_get_ooms.py \
  --batch 4 \
  --ns-batch-size 250 \
  --ns-workers 250 \
  --timeout 200
```

**Note:** `--batch` maintains constant parallelism. With `--batch 4`, the tool always processes 4 clusters simultaneously. When one finishes, the next one starts immediately.

#### Moderate parallelism for medium clusters
```bash
./oc_get_ooms.py \
  --batch 2 \
  --ns-batch-size 50 \
  --ns-workers 20 \
  --timeout 60
```

### Time Range Filtering

Filter events by time range (default: 1 day):

```bash
# Last 30 seconds (for testing)
./oc_get_ooms.py --time-range 30s

# Last 1 hour
./oc_get_ooms.py --time-range 1h

# Last 6 hours
./oc_get_ooms.py --time-range 6h

# Last 1 day (default)
./oc_get_ooms.py --time-range 1d

# Last 7 days
./oc_get_ooms.py --time-range 7d

# Last 1 month (30 days)
./oc_get_ooms.py --time-range 1M
```

**Time range formats:**
- `s` = seconds (e.g., `30s`, `120s`)
- `m` = minutes (e.g., `15m`, `30m`)
- `h` = hours (e.g., `1h`, `6h`, `24h`)
- `d` = days (e.g., `1d`, `7d`, `30d`)
- `M` = months (30 days, e.g., `1M`, `2M`)

### Namespace Filtering

#### Ephemeral Namespace Exclusion (Default on EaaS Clusters)

By default, the tool automatically excludes ephemeral test and cluster namespaces
to avoid false positives from temporary test environments on EaaS clusters.

**Ephemeral namespaces that are excluded by default:**
- **Ephemeral cluster namespaces**: `clusters-<uuid>` pattern
  - Example: `clusters-4e52ba17-c17b-4f35-b7e0-0215e63678a0`
- **Ephemeral test namespaces**: Common test patterns
  - `test-*`, `e2e-*`, `ephemeral-*`, `ci-*`, `pr-*`, `temp-*`, `tmp-*`
  - Namespaces ending with `-test`, `-e2e`, `-ephemeral`

To include ephemeral namespaces in the scan:
```bash
./oc_get_ooms.py --include-ephemeral
```

#### Include only specific namespaces
```bash
# Only namespaces containing "tenant"
./oc_get_ooms.py --include-ns tenant

# Multiple patterns (OR logic)
./oc_get_ooms.py --include-ns "tenant|prod"
```

#### Exclude specific namespaces
```bash
# Exclude test namespaces
./oc_get_ooms.py --exclude-ns test

# Exclude multiple patterns
./oc_get_ooms.py --exclude-ns "debug|sandbox|test"
```

#### Combine include and exclude
```bash
# Include tenant namespaces, but exclude test ones
./oc_get_ooms.py \
  --include-ns tenant \
  --exclude-ns test

# Complex filtering
./oc_get_ooms.py \
  --include-ns "tenant|prod" \
  --exclude-ns "debug|sandbox|test"
```

### Combining Options

#### Production cluster scan with custom settings
```bash
./oc_get_ooms.py \
  --contexts prod-cluster \
  --time-range 1d \
  --include-ns "tenant|prod" \
  --exclude-ns "test|debug" \
  --batch 4 \
  --ns-batch-size 100 \
  --ns-workers 50 \
  --timeout 120 \
  --retries 5
```

#### Quick scan of current context (last 6 hours)
```bash
./oc_get_ooms.py \
  --current \
  --time-range 6h
```

#### Comprehensive multi-cluster scan (last 7 days)
```bash
./oc_get_ooms.py \
  --contexts "prod,staging" \
  --time-range 7d \
  --batch 8 \
  --ns-batch-size 200 \
  --ns-workers 100
```

#### High-reliability scan with increased retries
```bash
./oc_get_ooms.py \
  --retries 5 \
  --timeout 300 \
  --time-range 1d
```

### Viewing Results

After running the tool, you'll find four output files:

```bash
# View CSV in spreadsheet
open oom_results.csv

# View JSON (pretty-printed)
cat oom_results.json | python -m json.tool

# View HTML report (double-click in Finder/File Explorer)
open oom_results.html

# View table format
cat oom_results.table
```

**Note:** If you ran the tool previously, your old files are automatically backed up with timestamps (e.g., `oom_results_13-Jan-2026_12-10-22-EDT.csv`). You can access historical data by opening the timestamped backup files.

The **HTML report** (`oom_results.html`) is particularly useful for:
- Quick visual overview
- Sharing with team members
- Presentation-ready reports
- Clickable links to artifact files

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
- **Time range filtering** to focus on recent events
- **Multiple detection methods** for comprehensive coverage:
  - Kubernetes events (optimized single API call)
  - Direct pod status checks (for currently existing pods)
- Namespaces printed **only if issues are found**

---

## 📌 Requirements

- Python **3.9+**
- `oc` CLI in PATH
- Logged in (`oc whoami` must succeed)

---

## 📄 Files Generated

| File | Purpose | Format |
|------|---------|--------|
| `oom_results.csv` | Spreadsheet-friendly output | CSV |
| `oom_results.json` | Structured automation input | JSON |
| `oom_results.html` | Visual report (open in browser) | HTML |
| `oom_results.table` | Human-readable text table | Plain text |
| `/tmp/<cluster>/*.txt` | Pod forensic artifacts | Text files |

**Note:** All output files are generated automatically in the current directory. The HTML file is fully self-contained and can be opened directly in any web browser by double-clicking it.

### Automatic File Backup

The tool automatically backs up existing output files before generating new ones. If any of the output files (`oom_results.csv`, `oom_results.json`, `oom_results.html`, `oom_results.table`) already exist, they are automatically renamed with a timestamp before new files are created.

**Backup Format:**
```
oom_results_<DD-MMM-YYYY_HH-MM-SS-TZ>.csv
oom_results_<DD-MMM-YYYY_HH-MM-SS-TZ>.json
oom_results_<DD-MMM-YYYY_HH-MM-SS-TZ>.html
oom_results_<DD-MMM-YYYY_HH-MM-SS-TZ>.table
```

**Example:**
- Existing file: `oom_results.csv`
- Backed up as: `oom_results_13-Jan-2026_12-10-22-EDT.csv`
- New file created: `oom_results.csv`

This ensures you can:
- Keep historical data from previous runs
- Always have the latest results in standard filenames
- Compare results across different time periods

The tool will display which files were backed up when you run it:
```
Backed up 4 existing file(s):
  → oom_results_13-Jan-2026_12-10-22-EDT.csv
  → oom_results_13-Jan-2026_12-10-22-EDT.json
  → oom_results_13-Jan-2026_12-10-22-EDT.html
  → oom_results_13-Jan-2026_12-10-22-EDT.table
```

---

## 🧠 Design Philosophy

> **Fast, safe, forensic-grade, and cluster-scale.**

**Recent Enhancements:**
- **Constant Parallelism**: Maintains optimal resource utilization across all clusters
- **Performance Optimized**: Single event API call per namespace (3x faster)
- **Comprehensive Detection**: Events + pod status checks ensure no OOM/CrashLoop pods are missed
- **Time Range Filtering**: Focus on recent events with configurable lookback window
- **Multi-Format Output**: CSV, JSON, HTML, and TABLE formats for maximum flexibility
- **HTML Reports**: Standalone visual reports with clickable artifact links
- **Consistent Output**: All formats are synchronized and include metadata

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
- Actual memory usage
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

### 📊 6. Enhanced HTML Reports (✅ Implemented)

HTML report generation is now available! The tool automatically generates `oom_results.html` with:
- Summary statistics
- Cluster-level grouping
- Namespace drilldowns
- Clickable artifact links
- Responsive design

Future enhancements could include:
- Interactive charts and graphs
- Trend visualization
- Export to PDF
- Customizable themes

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


