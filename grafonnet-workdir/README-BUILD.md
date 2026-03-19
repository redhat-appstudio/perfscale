# Building the dashboards

## Prerequisites

1. **Jsonnet** – e.g. [go-jsonnet](https://github.com/google/go-jsonnet/releases) (put `jsonnet` in your PATH).
2. **jq** – for formatting JSON output.
3. **Jsonnet Bundler (jb)** – to fetch the grafonnet library:
   - Go: `go install github.com/jsonnet-bundler/jsonnet-bundler/cmd/jb@latest`
   - Or download from: https://github.com/jsonnet-bundler/jsonnet-bundler/releases

## One-time setup

From this directory (`grafonnet-workdir`):

```bash
jb install
```

This creates `vendor/` with the grafonnet dependency.

## Build

From the **perfscale repo root** (parent of grafonnet-workdir):

```bash
./grafonnet-workdir/build.sh
```

Or from this directory:

```bash
./build.sh
```

Outputs are written to `generated/` (loadtest dashboards) and `../grafana/dashboards/` (controller dashboards).
