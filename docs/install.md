# Installation

`taxi-seed` is published to [PyPI](https://pypi.org/project/taxi-seed/) as a single distribution that carries all five CLIs plus the shared library. There are two ways to get it, and they are not interchangeable:

- **Install the released package** if you want to *run* the tools — mirror the TLC bucket, analyze drift, load a database.
- **Work from a clone** if you want to *develop* on the repo, or if you want the curated normalize mappings that live in the repo rather than in the wheel (see [Curated mappings are not in the wheel](#mappings) below).

Both require Python 3.12 or 3.13.

!!! warning "`taxi-download` is not in the current PyPI release"
    The published `0.1.0` predates the Python downloader — it exposes `normalize`, `schema-drift`, `taxi-curate-mappings`, `taxi-load`, and `taxi-run`, but not `taxi-download`. Until `v0.2.0` is tagged on `main`, install from a clone if you need the downloader. Everything else on this page describes the package as it stands in the repo today.

## From PyPI

=== "uv tool (recommended)"

    ```bash
    uv tool install taxi-seed
    ```

    Installs the package into its own isolated environment and puts every console script on your `PATH`. This is the right choice for a machine that runs the pipeline on a schedule.

=== "pip"

    ```bash
    python -m venv .venv
    source .venv/bin/activate      # Windows: .venv\Scripts\activate
    pip install taxi-seed
    ```

    No `uv` required — `uv` is a convenience for the development workflow, not a runtime dependency of the tools.

=== "uvx (no install)"

    ```bash
    uvx --from taxi-seed taxi-download yellow --recent 3
    ```

    Runs a single command in a throwaway environment. Useful for a one-off download or for trying a tool before committing to an install; `--from taxi-seed` is required because the package name and the command names differ.

Either of the first two puts these commands on your `PATH`:

| Command | What it does | Guide |
|---|---|---|
| `taxi-download` | Mirror TLC parquet from CloudFront to `raw/` | [Downloader](guides/downloader.md) |
| `schema-drift` | Report column-name and column-shape drift across a mirror | [Schema Drift](guides/schema-drift.md) |
| `normalize` | Rewrite a mirror to a single target schema | [Normalize](guides/normalize.md) |
| `taxi-load` | Load normalized parquet into a target database | [Loader](guides/loader.md) |
| `taxi-run` | Drive download → normalize → load as one pipeline | [Orchestrator](guides/orchestrator.md) |
| `taxi-curate-mappings` | Auto-accept detected drift into complete mapping YAMLs | [Orchestrator](guides/orchestrator.md) |

### Verify the install

```bash
taxi-download --help
```

If the command is not found after `uv tool install`, run `uv tool update-shell` (or add `~/.local/bin` to your `PATH`) and open a new shell.

### Upgrading

```bash
uv tool upgrade taxi-seed     # uv tool installs
pip install -U taxi-seed      # pip installs
```

### Installing a prerelease

Prerelease tags (`v0.2.0rc1`, `v0.2.0a1`, …) publish to [TestPyPI](https://test.pypi.org/project/taxi-seed/) instead of PyPI. TestPyPI does not mirror `duckdb`, `httpx`, or `pyyaml`, so you have to leave the runtime dependencies pointed at real PyPI:

```bash
pip install \
  --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ \
  taxi-seed==0.2.0rc1
```

## From a clone

```bash
git clone https://github.com/andrekamman/taxi-seed.git
cd taxi-seed
uv sync
uv run taxi-download yellow --recent 3
```

This is what [Getting Started](getting-started.md) walks through, and it is what [Contributing](contributing.md) assumes. `uv sync` materializes the exact locked versions from `uv.lock` into `.venv/`, which an install from PyPI does not do — a PyPI install resolves dependencies fresh against the ranges in `pyproject.toml`.

## Reading the examples

Every example in these docs is written for the clone workflow, so commands are prefixed with `uv run`:

```bash
uv run taxi-download yellow --recent 3
```

If you installed from PyPI, drop the prefix — the command names are identical:

```bash
taxi-download yellow --recent 3
```

## Curated mappings are not in the wheel {#mappings}

The repo carries hand-curated normalize mappings at `normalize/mappings/{yellow,green,fhv,fhvhv}.yaml`. **These are not packaged into the wheel** — the distribution contains Python modules only.

`normalize` (and the normalize stage of `taxi-run`) resolves its mapping as `normalize/mappings/<type>.yaml` *relative to the current working directory*, with no flag to point it elsewhere. So from a PyPI install in an arbitrary directory, the first `normalize yellow` finds no mapping, bootstraps a fresh scaffold into `./normalize/mappings/yellow.yaml`, and exits `3` for review — the documented first-run behavior, but starting from zero rather than from the curated file.

Two ways to get the curated mappings under an installed workflow:

1. Copy them into your working directory once:

    ```bash
    mkdir -p normalize/mappings
    curl -sSL -o normalize/mappings/yellow.yaml \
      https://raw.githubusercontent.com/andrekamman/taxi-seed/main/normalize/mappings/yellow.yaml
    ```

    Repeat per data type. Everything else (`raw/`, `raw-normalized/`) is already relative to `--data-dir`.

2. Or run `normalize` / `taxi-run` from a clone, and use the PyPI install only for `taxi-download`, `schema-drift`, and `taxi-load` — those three are fully self-contained and need no repo files.

See the [Normalize guide](guides/normalize.md) for what the mapping file contains and how bootstrap/amend semantics work.

## For maintainers

How a tag becomes a PyPI release — branch model, the `integration` gate, prerelease vs final classification, and the OIDC Trusted Publishing setup — is in [Operations → Releasing](operations/releasing.md).
