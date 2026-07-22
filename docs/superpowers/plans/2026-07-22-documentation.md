# Documentation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish a Material for MkDocs documentation site at `https://andrekamman.github.io/taxi/`, and fold in a downloader semantic tweak (`--recent [N] [TYPE]` + incremental stop-on-local) so the docs describe an actually-working feature.

**Architecture:** Docs live in `docs/` under the public repo, built by MkDocs Material and deployed to the `gh-pages` branch by a GitHub Actions workflow that runs after tests pass. Site nav mirrors the audience mental model (Home → Getting Started → Guides → Cookbook → Architecture → Reference → Design Specs → Contributing). Per-component READMEs shrink to one-paragraph pointers so content lives in exactly one place. Downloader change is a small bash edit that adds per-type argument parsing and changes the recent walker to stop on locally-encountered files.

**Tech Stack:** MkDocs 1.6+, mkdocs-material 9.5+, pymdown-extensions 10.7+, GitHub Actions, bash (downloader).

**Reference spec:** `docs/superpowers/specs/2026-07-22-documentation-design.md`

---

## Preconditions

- [ ] **Fresh clone of the PUBLIC repo.** All commits in this plan land on `andrekamman/taxi` (public). The current local checkout at `/Users/andre/git/taxi/` points at the private `taxi-dev` remote and MUST NOT be pushed to. Clone the public repo into a separate directory:
  ```bash
  git clone https://github.com/andrekamman/taxi.git /Users/andre/git/taxi-public
  cd /Users/andre/git/taxi-public
  git branch --show-current   # should print: main
  ```
  All subsequent commands in this plan assume `cwd=/Users/andre/git/taxi-public`.

- [ ] **Baseline tests green.**
  ```bash
  uv sync --extra test
  uv run --extra test pytest -q
  ```
  Expected: 83 passed.

- [ ] **Copy the design spec + this plan** from the private repo into the public repo (they live in `docs/superpowers/specs/` and `docs/superpowers/plans/` respectively; the private repo's copies are already committed at SHAs from the taxi-dev history). Simplest: `cp` the two files across, `git add`, `git commit -m "docs: import documentation design spec + plan from taxi-dev"`. This gives the public repo the same design-record trail and makes the nav's Design Specs section wire up cleanly in later commits.

---

## Task 0: Downloader semantic change — `--recent [N] [TYPE]` + stop-on-local

**Motivation:** Section "In-scope code change" of the spec. This lands first so the docs can describe an actually-working feature.

**Files:**
- Modify: `downloader/download_taxi_data.sh`

### Steps

- [ ] **0.1 Read the current script** to see the sections being replaced.

  ```bash
  sed -n '6,32p' downloader/download_taxi_data.sh   # argument-parsing block
  sed -n '305,375p' downloader/download_taxi_data.sh   # download_recent_type function
  sed -n '390,420p' downloader/download_taxi_data.sh   # bottom dispatch block
  ```

  Confirm the block starting `while [[ $# -gt 0 ]]; do` is at lines 11–31 (argument parsing), `download_recent_type()` starts at line 306, and the final dispatch (`if [ $recent_only -eq 1 ]; then ... download_recent_type "yellow" ... download_full_type "yellow" 2009 1 ...`) is at lines 374–398. Approximate; exact line numbers may drift slightly. Adjust the edits below if so.

- [ ] **0.2 Replace the argument-parsing block**

  Replace the top-of-file section:
  ```bash
  # Parse flags
  recent_only=0
  recent_months=3
  max_lookback_months=18  # in --recent mode, how far back to look per type before giving up

  while [[ $# -gt 0 ]]; do
      case $1 in
          --recent)
              recent_only=1
              if [[ -n "$2" && "$2" =~ ^[0-9]+$ ]]; then
                  recent_months=$2
                  shift
              fi
              shift
              ;;
          -h|--help)
              echo "Usage: $0 [--recent [N]]"
              echo "  --recent [N]  Download only the last N months (default 3) per data type, newest first"
              exit 0
              ;;
          *)
              echo "Unknown option: $1"
              exit 1
              ;;
      esac
  done
  ```

  with:
  ```bash
  # Parse flags
  recent_only=0
  recent_months=3
  max_lookback_months=18  # in --recent mode, how far back to look per type before giving up
  recent_type=""          # empty = all four types
  full_type=""            # empty = all four types (full-history mode)

  DATA_TYPES=("yellow" "green" "fhv" "fhvhv")

  is_data_type() {
      local candidate="$1"
      for t in "${DATA_TYPES[@]}"; do
          [[ "$candidate" == "$t" ]] && return 0
      done
      return 1
  }

  while [[ $# -gt 0 ]]; do
      case $1 in
          --recent)
              recent_only=1
              shift
              # Optional numeric N
              if [[ $# -gt 0 && "$1" =~ ^[0-9]+$ ]]; then
                  recent_months=$1
                  shift
              fi
              # Optional TYPE
              if [[ $# -gt 0 ]] && is_data_type "$1"; then
                  recent_type=$1
                  shift
              fi
              ;;
          -h|--help)
              cat <<'HELP'
  Usage:
    ./download_taxi_data.sh                          Full history, all four types
    ./download_taxi_data.sh TYPE                     Full history, one type only
    ./download_taxi_data.sh --recent [N]             Recent N months (default 3), all types
    ./download_taxi_data.sh --recent [N] TYPE        Recent N months (default 3), one type only
    ./download_taxi_data.sh --recent TYPE            Recent 3 months, one type only

  TYPE is one of: yellow, green, fhv, fhvhv.

  Recent-mode walker semantics:
    Walks backward from the previous month. A remotely-not-yet-published month
    is skipped without counting. A locally-already-existing file stops the walker
    (assumes prior runs downloaded everything older). Downloads count toward N.
  HELP
              exit 0
              ;;
          *)
              if is_data_type "$1"; then
                  full_type=$1
                  shift
              else
                  echo "Unknown option: $1" >&2
                  echo "Run '$0 --help' for usage." >&2
                  exit 1
              fi
              ;;
      esac
  done
  ```

- [ ] **0.3 Change `download_recent_type` walker semantic**

  Read the current function body first (it's ~65 lines starting around line 306). Then make the following surgical semantic changes; adjust indentation as needed for the shell to parse cleanly.

  **Change A: local-file-encountered → break out of the loop.**

  In the current function, the `if [ -f "$target_path" ]; then ... else ... fi` block reads roughly:
  ```bash
          if [ -f "$target_path" ]; then
              echo "  Already have $filename"
              ((got++))
          else
              local url="https://d37ci6vzurychx.cloudfront.net/trip-data/${filename}"
              echo "  Trying $filename..."
              local status
              status=$(download_one "$url" "$target_path")
              case "$status" in
                  ok) ... ;;
                  notfound) ... ;;
                  ratelimit) ... ;;
                  neterror) ... ;;
              esac
          fi
  ```
  Restructure it as an `if` that breaks (no `else`) followed by the download logic at the same indentation level:
  ```bash
          if [ -f "$target_path" ]; then
              echo "  Already have $filename — stopping (assume prior runs are caught up)"
              break
          fi
          local url="https://d37ci6vzurychx.cloudfront.net/trip-data/${filename}"
          echo "  Trying $filename..."
          local status
          status=$(download_one "$url" "$target_path")
          case "$status" in
              ok) ... ;;
              notfound) ... ;;
              ratelimit) ... ;;
              neterror) ... ;;
          esac
  ```
  The bodies of each `case` arm are unchanged. Adjust the indentation of the download-logic lines from 8 spaces to whatever the surrounding while-body uses (usually 4 spaces).

  **Change B: replace the loop's tail summary.**

  Locate the block near the end of `download_recent_type`:
  ```bash
      if [ $got -lt $want ]; then
          echo "  Got $got/$want months for $data_type (looked back $walked months)"
      else
          echo "  Got $got/$want months for $data_type"
      fi
  ```
  Replace with a single unconditional line, since `got` now counts only new downloads and the "caught up" case is handled by the earlier `break`:
  ```bash
      echo "  Downloaded $got new file(s) for $data_type (walked back $walked month(s))"
  ```

- [ ] **0.4 Change the bottom dispatch block**

  Locate the block at the end of the script starting with `# Recent mode: download inline per type...`. Replace:
  ```bash
  # Recent mode: download inline per type, walking back for unpublished months.
  if [ $recent_only -eq 1 ]; then
      echo "Downloading recent $recent_months months per data type (newest first)..."
      echo "Will walk back through older months for anything not yet published."
      echo ""

      download_recent_type "yellow"
      download_recent_type "green"
      download_recent_type "fhv"
      download_recent_type "fhvhv"

      echo "Download complete!"
      echo "Files saved to: ${output_dir}/<type>/<year>/"
      exit 0
  fi

  # Full mode: catch up on all history, per type. Each walker stops when it hits
  # the end of that type's published series and moves on.
  echo "Catching up all history per data type..."
  echo "Files saved to: ${output_dir}/<type>/<year>/"
  echo ""

  download_full_type "yellow" 2009 1
  download_full_type "green"  2013 8
  download_full_type "fhv"    2015 1
  download_full_type "fhvhv"  2019 2

  echo "Download complete!"
  ```

  with:
  ```bash
  # Recent mode: download inline per type, walking back for unpublished months,
  # stopping when a local file is encountered (incremental catch-up).
  if [ $recent_only -eq 1 ]; then
      echo "Downloading recent $recent_months months$([ -n "$recent_type" ] && echo " ($recent_type)")..."
      echo "Will walk back through older not-yet-published months, and stop"
      echo "at the first locally-existing file (assumes prior runs are caught up)."
      echo ""

      if [ -n "$recent_type" ]; then
          download_recent_type "$recent_type"
      else
          download_recent_type "yellow"
          download_recent_type "green"
          download_recent_type "fhv"
          download_recent_type "fhvhv"
      fi

      echo "Download complete!"
      echo "Files saved to: ${output_dir}/<type>/<year>/"
      exit 0
  fi

  # Full mode: catch up on all history. Each walker stops when it hits the
  # end of that type's published series and moves on.
  echo "Catching up all history$([ -n "$full_type" ] && echo " ($full_type)")..."
  echo "Files saved to: ${output_dir}/<type>/<year>/"
  echo ""

  case "$full_type" in
      "")
          download_full_type "yellow" 2009 1
          download_full_type "green"  2013 8
          download_full_type "fhv"    2015 1
          download_full_type "fhvhv"  2019 2
          ;;
      yellow)  download_full_type "yellow" 2009 1 ;;
      green)   download_full_type "green"  2013 8 ;;
      fhv)     download_full_type "fhv"    2015 1 ;;
      fhvhv)   download_full_type "fhvhv"  2019 2 ;;
  esac

  echo "Download complete!"
  ```

- [ ] **0.5 Syntax check**
  ```bash
  bash -n downloader/download_taxi_data.sh && echo "SYNTAX OK"
  ```
  Expected: `SYNTAX OK`.

- [ ] **0.6 Help output check**
  ```bash
  ./downloader/download_taxi_data.sh --help
  ```
  Expected: prints the 6-line usage block starting with `Usage:` and lists the four data types.

- [ ] **0.7 Argument parsing smoke — reject an unknown type**
  ```bash
  ./downloader/download_taxi_data.sh --recent 3 uber 2>&1 | head -3
  ```
  Expected: `Unknown option: uber` on stderr, exit code 1. (`uber` is a valid-looking token that isn't in DATA_TYPES.)

- [ ] **0.8 Argument parsing smoke — accept `--recent 3 yellow`**

  This will do a real network call. It's fine — the workflow is short:
  ```bash
  ./downloader/download_taxi_data.sh --recent 3 yellow 2>&1 | head -15
  ```

  Expected output pattern (first few lines):
  ```
  Checking for corrupt parquet files...
  ...
  NYC TLC Trip Data Downloader
  Generating URL list up to: 2026-06
  ...
  Downloading recent 3 months (yellow)...
  Will walk back through older not-yet-published months, and stop
  at the first locally-existing file (assumes prior runs are caught up).

  --- yellow: looking for 3 recent months ---
    Trying yellow_tripdata_2026-06.parquet...
  ```
  Kill with Ctrl-C after you see `--- yellow: looking for 3 recent months ---` — no need to actually download.

- [ ] **0.9 Argument parsing smoke — bare TYPE means full history for that type**
  ```bash
  ./downloader/download_taxi_data.sh yellow 2>&1 | head -10
  ```
  Expected: the "Catching up all history (yellow)..." banner and the yellow full-catchup start line. Kill early.

- [ ] **0.10 Confirm the stop-on-local behavior**

  Create a mock local file to prove the walker respects it:
  ```bash
  mkdir -p raw/yellow/2026
  touch raw/yellow/2026/yellow_tripdata_2026-05.parquet
  ./downloader/download_taxi_data.sh --recent 3 yellow 2>&1 | grep -A 2 "yellow: looking"
  ```
  Expected output includes:
  ```
  --- yellow: looking for 3 recent months ---
    Trying yellow_tripdata_2026-06.parquet...
      Not published yet, trying older month
    Already have yellow_tripdata_2026-05.parquet — stopping (assume prior runs are caught up)
    Downloaded 0 new file(s) for yellow (walked back 2 month(s))
  ```

  Clean up:
  ```bash
  rm raw/yellow/2026/yellow_tripdata_2026-05.parquet
  ```
  (Only remove the fake file we just touched; leave any real downloads.)

- [ ] **0.11 Confirm existing tests still pass**

  Even though this is a bash change with no Python tests, the pytest suite should remain green:
  ```bash
  uv run --extra test pytest -q
  ```
  Expected: 83 passed.

- [ ] **0.12 Commit**

  ```bash
  git add downloader/download_taxi_data.sh
  git commit -m "feat(downloader): --recent [N] [TYPE] with stop-on-local semantics"
  ```

---

## Task 1: MkDocs scaffolding + CI + Home page

**Motivation:** Get the site building and deploying before any content is written. From this commit on, every content commit deploys automatically.

**Files:**
- Create: `mkdocs.yml`
- Create: `docs/index.md`
- Modify: `pyproject.toml` (add `docs` optional-dependencies group)
- Create: `.github/workflows/ci.yml`
- Modify: `.gitignore` (add `site/`)

### Steps

- [ ] **1.1 Add the `docs` dependency group to `pyproject.toml`**

  Find the block:
  ```toml
  [project.optional-dependencies]
  test = ["pytest>=8.0"]
  ```

  Replace with:
  ```toml
  [project.optional-dependencies]
  test = ["pytest>=8.0"]
  docs = [
      "mkdocs>=1.6",
      "mkdocs-material>=9.5",
      "pymdown-extensions>=10.7",
  ]
  ```

- [ ] **1.2 Add `site/` to `.gitignore`**

  Append:
  ```
  # MkDocs build output
  site/
  ```

- [ ] **1.3 Create `mkdocs.yml`**

  Exact contents:
  ```yaml
  site_name: taxi
  site_description: Downloader, schema-drift analyzer, and K6 SQL Server load tester for NYC TLC taxi trip data
  site_url: https://andrekamman.github.io/taxi/
  repo_url: https://github.com/andrekamman/taxi
  repo_name: andrekamman/taxi
  edit_uri: edit/main/docs/

  theme:
    name: material
    features:
      - content.code.copy
      - content.tabs.link
      - navigation.tracking
      - navigation.top
      - search.highlight
      - search.share
    palette:
      - media: "(prefers-color-scheme: light)"
        scheme: default
        toggle:
          icon: material/brightness-7
          name: Switch to dark mode
      - media: "(prefers-color-scheme: dark)"
        scheme: slate
        toggle:
          icon: material/brightness-4
          name: Switch to light mode

  markdown_extensions:
    - admonition
    - attr_list
    - footnotes
    - md_in_html
    - pymdownx.highlight:
        linenums: true
        anchor_linenums: true
    - pymdownx.superfences:
        custom_fences:
          - name: mermaid
            class: mermaid
            format: !!python/name:pymdownx.superfences.fence_code_format
    - pymdownx.tabbed:
        alternate_style: true

  nav:
    - Home: index.md
  ```

- [ ] **1.4 Create `docs/index.md`** — the Home page

  Approximate length target: 130–160 lines. Real content required (no placeholders). Structure and specific facts to include:

  **Sections in order:**
  1. **H1 heading**: `taxi`
  2. **Hero paragraph (1–2 sentences)**: what this repo is. Real facts to include: WAF-aware downloader; schema-drift analyzer with heuristic + data-verified rename detection; K6-based SQL Server load tester; a normalizer that halts on data loss unless explicitly acknowledged. Mention MIT license and that it grew out of but improves substantially on `toddwschneider/nyc-taxi-data`.
  3. **H2 "Why this repo"**: three-bullet differentiators. Each bullet is a full sentence, not a phrase.
     - Bullet A: the CloudFront WAF classifier (mention "distinguishes 403-AccessDenied from WAF blocks; exponential backoff of 5→15→60 min; incremental catch-up").
     - Bullet B: the normalizer's data-loss-is-an-error contract (mention "ack_date is the only required field; bootstrap+amend so scheduled runs auto-detect new drift").
     - Bullet C: the K6 SQL Server load tester with both real-parquet and synthetic modes.
  4. **H2 "How it compares"**: markdown table with columns [Feature, this repo, `toddwschneider/nyc-taxi-data`, `duckdb httpfs`]. Rows: primary use case, resumable download, WAF-aware retry, schema handling, target database, load testing, install effort. 6 or 7 rows.
  5. **H2 "Quick start"**: 3-command copy-paste block that gets to a working parquet mirror. Exact commands:
     ```bash
     git clone https://github.com/andrekamman/taxi.git
     cd taxi
     ./downloader/download_taxi_data.sh --recent 3 yellow
     ```
     Followed by a callout that this fetches ~200 MB / 1–2 min, and a "→ Full tutorial" link to `getting-started.md`.
  6. **H2 "Requirements"**: bullet list — Python 3.12+, uv, bash 4+ / curl, disk (link to the downloader guide's sizing table), and note that individual guides list per-tool prerequisites.
  7. **H2 "Where to next"**: three cards or a table with links to Getting Started, Guides overview, and Cookbook.
  8. **H2 "Acknowledgments"**: existing `THIRD_PARTY_NOTICES` note (Todd Schneider MIT), link.

  Every code fence uses triple-backtick + language tag (e.g., ` ```bash `, ` ```sql `, ` ```yaml `). Every internal doc link uses relative paths (`getting-started.md`, `guides/downloader.md`) so MkDocs can validate them.

- [ ] **1.5 Create `.github/workflows/ci.yml`**

  ```yaml
  name: CI
  on:
    push:
      branches: [main]
    pull_request:

  jobs:
    test:
      runs-on: ubuntu-latest
      strategy:
        matrix:
          python-version: ["3.12", "3.13"]
      steps:
        - uses: actions/checkout@v4
        - uses: astral-sh/setup-uv@v3
          with:
            enable-cache: true
        - name: Set up Python ${{ matrix.python-version }}
          run: uv python install ${{ matrix.python-version }}
        - name: Install dependencies
          run: uv sync --extra test
        - name: Run tests
          run: uv run --extra test pytest -q

    docs:
      if: github.event_name == 'push' && github.ref == 'refs/heads/main'
      needs: test
      runs-on: ubuntu-latest
      permissions:
        contents: write
      steps:
        - uses: actions/checkout@v4
          with:
            fetch-depth: 0    # gh-deploy needs full history to push to gh-pages
        - uses: astral-sh/setup-uv@v3
          with:
            enable-cache: true
        - run: uv python install 3.12
        - run: uv sync --extra docs
        - name: Configure git identity
          run: |
            git config user.name "github-actions[bot]"
            git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
        - name: Deploy site
          run: uv run --extra docs mkdocs gh-deploy --force
  ```

- [ ] **1.6 Verify locally before committing**

  Install docs deps:
  ```bash
  uv sync --extra docs
  ```

  Build the site once to catch config errors:
  ```bash
  uv run --extra docs mkdocs build --strict
  ```
  Expected: `INFO - Documentation built in <n>.<nn> seconds` with no warnings or errors. `--strict` fails the build on any warning (broken link, missing page, etc.).

  Optionally serve locally to spot-check:
  ```bash
  uv run --extra docs mkdocs serve
  ```
  Then open `http://127.0.0.1:8000/` in a browser. Ctrl-C to stop.

  Cleanup the build artifact:
  ```bash
  rm -rf site/
  ```

- [ ] **1.7 Confirm the test suite still passes**
  ```bash
  uv run --extra test pytest -q
  ```
  Expected: 83 passed.

- [ ] **1.8 Commit**
  ```bash
  git add pyproject.toml uv.lock .gitignore mkdocs.yml docs/index.md .github/workflows/ci.yml
  git commit -m "docs: MkDocs scaffolding, Home page, CI workflow"
  ```

- [ ] **1.9 Post-commit (one-time manual steps)**

  These are NOT part of the commit; they're one-time configuration you do after the first push:
  1. Push to trigger the workflow:
     ```bash
     git push origin main
     ```
  2. Wait for the `docs` job to succeed (visible in the Actions tab). It will create the `gh-pages` branch and push to it.
  3. Enable GitHub Pages:
     ```bash
     gh api -X POST repos/andrekamman/taxi/pages -f source[branch]=gh-pages -f source[path]=/
     ```
  4. Set the About-sidebar homepage:
     ```bash
     gh repo edit andrekamman/taxi --homepage https://andrekamman.github.io/taxi/
     ```
  5. Wait ~1 min for Pages to publish. Verify `https://andrekamman.github.io/taxi/` loads and shows the Home page.

---

## Task 2: Getting Started tutorial

**Motivation:** The path from clean laptop to normalized parquet, in 10 minutes.

**Files:**
- Create: `docs/getting-started.md`
- Modify: `mkdocs.yml` (add to nav)

### Steps

- [ ] **2.1 Create `docs/getting-started.md`**

  Length target: 250–320 lines. Structure and specific facts:

  **Sections in order:**
  1. **H1**: `Getting Started`
  2. **Intro paragraph (2–3 sentences)**: what this tutorial covers, expected reader (data engineer trying the tools locally), expected time (~10 min end-to-end for the yellow-only path).
  3. **H2 "Prerequisites"** with checkboxes or a table:
     - Python 3.12 or 3.13.
     - [uv](https://github.com/astral-sh/uv) installed (link).
     - curl (present on macOS and most Linux; on Windows, install [Git for Windows](https://gitforwindows.org/) and run everything in Git Bash).
     - ~300 MB free disk for the tutorial (`--recent 3 yellow` is ~200 MB; the venv + wheels are ~100 MB).
     - Optional: DuckDB CLI for the "look at what you got" step (not required; can do the same via Python).
     - **Windows note (WSL)**: link to Downloader guide's WSL warning section.
  4. **H2 "1. Clone and install"** (title as literal H2):
     - Show the three commands:
       ```bash
       git clone https://github.com/andrekamman/taxi.git
       cd taxi
       uv sync --extra test
       ```
     - Explain what `--extra test` does (installs pytest; the alternative `uv sync` alone skips it).
     - Include an admonition (`!!! tip`) that says the docs work in `--extra docs` too if you want to browse locally.
  5. **H2 "2. Verify install"**:
     ```bash
     uv run --extra test pytest -q
     ```
     Expected: `83 passed in <n>.<nn>s` (as of writing; the count may grow as tests are added).
  6. **H2 "3. Download three recent months of yellow"**:
     ```bash
     ./downloader/download_taxi_data.sh --recent 3 yellow
     ```
     Include an actual output snippet showing 5–10 lines of what the user should see (Checking for corrupt parquet files → NYC TLC Trip Data Downloader → Downloading recent 3 months (yellow) → --- yellow: looking for 3 recent months → per-file "Saved" lines → "Downloaded N new file(s) for yellow"). Note: expected download is ~200 MB, ~1–2 min on residential broadband.
     Include an admonition (`!!! warning`) noting the WSL VHDX gotcha with a link to the downloader guide for details.
  7. **H2 "4. Look at what you got"**:
     - `find raw/yellow -name '*.parquet'` → expected output with paths.
     - `du -sh raw/yellow` → expected output (~200 MB).
     - DuckDB DESCRIBE:
       ```bash
       uv run python -c "import duckdb; import glob; f = sorted(glob.glob('raw/yellow/**/*.parquet', recursive=True))[-1]; print(f); print('---'); [print(r) for r in duckdb.execute(f\"DESCRIBE SELECT * FROM '{f}'\").fetchall()]"
       ```
       Show sample output (list of column tuples).
  8. **H2 "5. (Optional) Run schema-drift"**:
     ```bash
     uv run schema-drift --types yellow
     ```
     Show a 15-line snippet of a real report. Explain what to look for.
  9. **H2 "6. (Optional) Normalize"**:
     ```bash
     uv run normalize yellow
     ```
     Show the "no mapping found — analyzed" output and the SUGGESTED entries block. Explain that on this small sample, mostly-null historical columns are auto-dropped and `--recent 3` mostly hits stable-schema recent months so there's typically nothing to acknowledge. Show the second-run output where normalize actually writes to `raw-normalized/yellow/`.
  10. **H2 "Where to next"**:
      - Table linking to per-tool guides (Downloader, Schema Drift, Normalize, K6 Load Test).
      - Cross-link to Cookbook for common scenarios.
      - Cross-link to Architecture for the "how does this fit together" reader.

  Every code block is complete and copy-pasteable. Every "expected output" block shows real text, not sanitized ideal text. Where the output is very long, use a code-block-with-line-numbers and show only the first 10–15 lines with a "..." annotation.

- [ ] **2.2 Add to `mkdocs.yml` nav**

  Extend the `nav:` block:
  ```yaml
  nav:
    - Home: index.md
    - Getting Started: getting-started.md
  ```

- [ ] **2.3 Verify build**
  ```bash
  uv run --extra docs mkdocs build --strict
  ```
  Expected: `Documentation built` with no warnings. Broken internal links or unresolved images fail here.

- [ ] **2.4 Commit**
  ```bash
  git add docs/getting-started.md mkdocs.yml
  git commit -m "docs: getting-started tutorial"
  ```

---

## Task 3: Downloader guide + shrink component README

**Files:**
- Create: `docs/guides/downloader.md`
- Modify: `downloader/README.md` (shrink to pointer)
- Modify: `mkdocs.yml`

### Steps

- [ ] **3.1 Create `docs/guides/downloader.md`**

  Length target: 250–300 lines. Structure:

  1. **H1**: `Downloader`
  2. **Intro (2–3 paragraphs)**: what it does, why it exists (the CloudFront WAF-403 ambiguity problem), when NOT to use it (link to alternatives — DuckDB httpfs for ad-hoc queries).
  3. **H2 "Prerequisites"**: bash 4+, curl; note that macOS/Linux ship both, Windows needs Git for Windows. Disk sizing paragraph pointing at the table below.
  4. **H2 "Disk sizing"** — the sizing table from the spec (copy verbatim), with the "TLC adds ~2 GB/month" note.
  5. **H2 "Install"**: none; it's a bash script.
  6. **H2 "Basic usage"** — copy-paste the four canonical invocations from the `--help` output:
     - Full history, all four types.
     - Full history, one type.
     - Recent N months (default 3), all types.
     - Recent N months, one type.
     - Recent 3 (default), one type.
  7. **H2 "Windows / WSL"**:
     - Git for Windows setup (~3 sentences).
     - The WSL VHDX warning explained in detail: what a VHDX is, why it grows, why deleting files doesn't shrink it. Mention the `OUTPUT_DIR=/mnt/c/Users/$USER/taxi-data` recommendation with a full working command. Explain the interactive `[y/N]` prompt and the non-interactive fallback (proceeds without prompting).
  8. **H2 "What makes it different"** — the technical differentiators, with a mermaid diagram for the WAF classifier:
     ````
     ```mermaid
     flowchart TD
       A[curl request] -->|HTTP 200 or 206| B{starts with PAR1?}
       B -->|yes| C[ok — download]
       B -->|no| D[ratelimit — intercept page]
       A -->|HTTP 403| E{body contains?}
       E -->|AccessDenied XML| F[notfound — not published yet]
       E -->|CloudFront HTML| G[ratelimit — WAF block]
       E -->|other| F
       A -->|HTTP 404| F
       A -->|HTTP 429/502/503/504| G
       A -->|timeout/reset| G
     ```
     ````
     Then the exponential backoff explanation (5 → 15 → 60 min ladder, resets on any successful download).
     Then the boundary auto-termination explanation (walks forward, stops on `notfound` after seeing data → end of published series).
     Then the PAR1 head+tail validation (footer + magic bytes, catches truncated downloads and WAF intercepts).
  9. **H2 "Recent-mode semantics"** (post-Task-0 behavior):
     - Explain the walker: walks backward from prev_month; not-published skips without penalty; local-file-encountered stops the walker (incremental catch-up).
     - Three worked examples from the spec: fresh checkout, monthly cron re-run, already-caught-up.
     - Note that if you delete a local file mid-history, subsequent `--recent` runs won't backfill it (they stop at the newer files above). Use `./download_taxi_data.sh yellow` to catch up everything.
  10. **H2 "Output layout"**: `raw/<type>/<year>/<type>_tripdata_YYYY-MM.parquet`. Show a sample `find` listing.
  11. **H2 "Configuration"**: the `OUTPUT_DIR` env var. When to use it (WSL, external drives, NAS mount points). Include a corporate-proxy note (curl reads `HTTPS_PROXY` env var).
  12. **H2 "Alternatives"** — comparison table:
      - `toddwschneider/nyc-taxi-data`: canonical wget loop; when to use (Postgres/ClickHouse importer target); when not (needs resumable + WAF handling).
      - `DuckDB httpfs`: query TLC parquet directly from CloudFront; when to use (ad-hoc analytics, no local mirror needed); when not (repeated full-scans, offline work). Include a working DuckDB code snippet:
        ```sql
        INSTALL httpfs; LOAD httpfs;
        SELECT count(*) FROM read_parquet('https://d37ci6vzurychx.cloudfront.net/trip-data/yellow_tripdata_2024-01.parquet');
        ```
      - HuggingFace mirrors: fine for exploratory, stale for production.
  13. **H2 "Troubleshooting"**:
      - "I'm getting rate-limited constantly" → check the backoff ladder is engaging; if it aborts after 3 escalations, the WAF has flagged your IP for a longer window than 60 min; try again in a few hours.
      - "The script says the file is corrupt and re-downloads it every time" → probably a partial download saved. The PAR1 tail check catches this on next run.
      - "I want to point downloads at S3 instead of local disk" → not supported; the script writes to a local path. PR welcome.
      - "Full history took longer than the estimate" → residential broadband varies; FHVHV files are the biggest.

- [ ] **3.2 Shrink `downloader/README.md`** to a pointer.

  Replace the entire file with:
  ```markdown
  # downloader

  Bash script that mirrors NYC TLC parquet trip data from CloudFront to a local `raw/` directory. WAF-aware retry, boundary auto-termination, incremental catch-up.

  → **[Full guide](https://andrekamman.github.io/taxi/guides/downloader/)**
  ```

- [ ] **3.3 Add to `mkdocs.yml` nav**

  Extend the nav:
  ```yaml
  nav:
    - Home: index.md
    - Getting Started: getting-started.md
    - Guides:
        - Downloader: guides/downloader.md
  ```

- [ ] **3.4 Verify build**
  ```bash
  uv run --extra docs mkdocs build --strict
  ```

- [ ] **3.5 Commit**
  ```bash
  git add docs/guides/downloader.md downloader/README.md mkdocs.yml
  git commit -m "docs: downloader guide + shrink component README"
  ```

---

## Task 4: Schema Drift guide + shrink component README

**Files:**
- Create: `docs/guides/schema-drift.md`
- Modify: `schema-drift/README.md`
- Modify: `mkdocs.yml`

### Steps

- [ ] **4.1 Create `docs/guides/schema-drift.md`**

  Length target: 180–220 lines. Structure:

  1. **H1**: `Schema Drift`
  2. **Intro (1–2 paragraphs)**: what it does. Concrete: analyzes a family of parquet files representing different time periods, identifies added/removed/type-changed columns at transition points, and suggests renames via heuristic + data verification.
  3. **H2 "When to use it"**: two scenarios — before ingesting historical TLC data (know what you're going to hit); before writing a `normalize` mapping (see what schema-drift suggests as renames).
  4. **H2 "Prerequisites"**: `uv sync` (that's it); a parquet family (either the downloader's `raw/` mirror or any directory of parquet with a `<type>_tripdata_YYYY-MM.parquet`-style naming convention).
  5. **H2 "Basic usage"**:
     - Default (all four types, `raw/`): `uv run schema-drift`.
     - One type: `uv run schema-drift --types yellow`.
     - Write to a file: `uv run schema-drift --output drift-report.txt`.
  6. **H2 "Three modes"** — decision guide:
     - **Default (taxi mode)**: uses the built-in NYC-TLC-specific abbreviation dictionary and semantic categories (pickup vs dropoff, coordinate, location_id, amount, datetime). Fast. Best for TLC data.
     - **`--generic`**: no domain knowledge; uses only data-similarity between columns. Slower (samples rows). Best for other datasets; results explicitly marked "requires human review".
     - **`--verify-data`**: taxi mode + sampled data comparison to verify low-confidence renames. Slower than default. Best when you plan to trust the output programmatically (e.g., feeding into `normalize bootstrap`).
     - Include a decision-tree bullet: "TLC data + first look → default. Other dataset → `--generic`. TLC data + acting on the output → `--verify-data`."
  7. **H2 "Reading the report"** — annotated example. Include a real 30–40 line report snippet from a `schema-drift yellow` run, annotated with what each section means: initial schema, transitions, renames (with confidence + verification status), added/removed columns, type changes, cross-type summary.
  8. **H2 "How rename detection works"** — explain the heuristic clearly:
     - **Semantic categories**: `pickup`/`dropoff`/`coordinate`/`location_id`/`amount`/`datetime`. Columns in conflicting categories never match (a `pickup_datetime` will never be suggested as a rename of `dropoff_datetime`).
     - **Name similarity**: longest-common-subsequence + token-based similarity with abbreviation expansion (e.g., `amt` → `amount`, `pu` → `pickup`).
     - **Data verification**: for candidate pairs, compare null ratios, cardinality, min/max ranges, and top values in a sampled window. High agreement → high confidence.
     - Mention the confidence threshold (0.6 by default) and how to reason about the score.
  9. **H2 "Programmatic API"**: how to import and call `schema_drift.analyze.analyze_data_type()` from a Python script or notebook. Include a working 15-line snippet.
  10. **H2 "Known limits"**: heuristic, not proof; needs human review for low-confidence renames; particularly weak when two columns are statistically indistinguishable (e.g., two timestamp columns with identical ranges).

- [ ] **4.2 Shrink `schema-drift/README.md`**

  Replace with:
  ```markdown
  # schema-drift

  Analyzes a family of parquet files for schema changes over time. Identifies added/removed/type-changed columns and suggests renames via heuristic + data-based verification.

  → **[Full guide](https://andrekamman.github.io/taxi/guides/schema-drift/)**
  ```

- [ ] **4.3 Update `mkdocs.yml` nav**

  Extend:
  ```yaml
  nav:
    - Home: index.md
    - Getting Started: getting-started.md
    - Guides:
        - Downloader: guides/downloader.md
        - Schema Drift: guides/schema-drift.md
  ```

- [ ] **4.4 Verify build** — same command as previous tasks.
- [ ] **4.5 Commit**
  ```bash
  git add docs/guides/schema-drift.md schema-drift/README.md mkdocs.yml
  git commit -m "docs: schema-drift guide + shrink component README"
  ```

---

## Task 5: Normalize guide + shrink component README

**Files:**
- Create: `docs/guides/normalize.md`
- Modify: `normalize/README.md`
- Modify: `mkdocs.yml`

### Steps

- [ ] **5.1 Create `docs/guides/normalize.md`**

  Length target: 320–380 lines (deepest guide). Structure:

  1. **H1**: `Normalize`
  2. **Intro (2–3 paragraphs)**: The core contract stated up front — data loss is a first-class error, not a warning. Historical parquet files get rewritten to match the latest schema; any silent drop or lossy cast requires explicit human acknowledgment via `ack_date`.
  3. **H2 "Prerequisites"**: `raw/<type>/` mirror from the downloader; `uv sync`.
  4. **H2 "Install"**: `uv sync`.
  5. **H2 "The three-state model"** — with a mermaid state diagram:
     ````
     ```mermaid
     stateDiagram-v2
       [*] --> First_run: normalize yellow
       First_run --> Awaiting_edits: no mapping found → auto-bootstrap → exit 3
       Awaiting_edits --> Second_run: human edits YAML
       Second_run --> Unresolved: unresolved items → auto-amend → exit 1
       Second_run --> Complete: mapping complete
       Unresolved --> Awaiting_edits: human re-edits
       Complete --> [*]: normalized parquet written → exit 0
     ```
     ````
     Explain each state with a 2–3 sentence paragraph.
  6. **H2 "Mapping file"** — brief inline example (5 fields), pointing at Reference→Configuration for the exhaustive schema.
  7. **H2 "Bootstrap and amend"** — explain the two operations:
     - **Bootstrap** (fires when no mapping exists): analyzes raw + calls schema-drift; emits YAML with SUGGESTED renames + TODO ack items + a machine-generated timeline header.
     - **Amend** (fires when mapping exists but planning finds unresolved items): loads existing mapping, appends new SUGGESTED/TODO items for anything not already handled, rewrites the file preserving all existing entries. Human comments in the body do NOT survive amends (accepted trade-off).
     - Operational value: the orchestrator sub-project (planned) will run this on a cron; TLC ships new drift → orchestrator's scheduled run detects unresolved → amend adds new items → human wakes up to a mapping diff to review.
  8. **H2 "The `ack_date` convention"**: only `ack_date` is required. `ack_by` and `reason` are optional but recommended for git-history readability. Show a minimal ack and a well-documented ack side-by-side.
  9. **H2 "Exit codes"**:
     | Code | Meaning | What to do |
     |---|---|---|
     | 0 | Success — files normalized or already present | Nothing |
     | 1 | Mapping incomplete; unresolved items reported and mapping amended | Edit the mapping, re-run |
     | 2 | Configuration error (missing raw data, malformed mapping, target not found) | Fix the config error |
     | 3 | First run; scaffold generated | Review the scaffold, edit, re-run |
  10. **H2 "The `--sample` flag"**: what it controls (rows or percent sampled during rename verification during bootstrap/amend). Default 100% (full scan). Reduce for very large datasets. Metadata checks and precision scans always full-scan regardless.
  11. **H2 "Worked example 1: First-time yellow"** — real end-to-end walkthrough. Show:
      - Command: `uv run normalize yellow`.
      - Expected output (first-run block, exit 3).
      - Snippet of the generated `normalize/mappings/yellow.yaml` — the timeline header, the SUGGESTED renames block, the TODO acknowledged_data_loss block.
      - The human's edits (show a diff or the resulting file).
      - Second run: `uv run normalize yellow` → normalized parquet written.
      - `find raw-normalized/yellow -name '*.parquet'` to show output.
  12. **H2 "Worked example 2: New drift appears months later"** — the amend scenario:
      - Set the scene: it's 2027; TLC changed the format.
      - `uv run normalize yellow` → detects unresolved items, amends the mapping.
      - Show the amend diff (before/after excerpt of the YAML).
      - Human reviews, uncomments, re-runs → success.
  13. **H2 "What runs automatically (no mapping needed)"**: all-null column drops, missing-in-historical null-fill, safe widening casts.
  14. **H2 "What triggers an error"**: non-null column without mapping, lossy cast without ack.

- [ ] **5.2 Shrink `normalize/README.md`**

  Replace with:
  ```markdown
  # normalize

  Rewrites historical TLC parquet to conform to the latest schema. Data loss is a first-class error — halts unless every discarded column or lossy cast is explicitly acknowledged. Auto-bootstrap on first run; auto-amend on new drift.

  → **[Full guide](https://andrekamman.github.io/taxi/guides/normalize/)**
  ```

- [ ] **5.3 Update `mkdocs.yml` nav**

  Extend the Guides section:
  ```yaml
        - Normalize: guides/normalize.md
  ```

- [ ] **5.4 Verify build.**
- [ ] **5.5 Commit**
  ```bash
  git add docs/guides/normalize.md normalize/README.md mkdocs.yml
  git commit -m "docs: normalize guide + shrink component README"
  ```

---

## Task 6: K6 Load Test guide + shrink component README

**Files:**
- Create: `docs/guides/k6-loadtest.md`
- Modify: `k6-loadtest/README.md`
- Modify: `mkdocs.yml`

### Steps

- [ ] **6.1 Create `docs/guides/k6-loadtest.md`**

  Length target: 220–270 lines. Structure:

  1. **H1**: `K6 Load Test`
  2. **Intro**: what it does — K6-based SQL Server load tester with a Python preprocessor. Turns parquet (or synthetic specs) into a K6 test bundle (CREATE TABLE DDL, chunked JSON payloads, K6 `test.js`, manifest).
  3. **H2 "Prerequisites"**:
     - Go 1.22+ (for `xk6` to build the custom K6 binary with the `xk6-sql` extension and Grafana's SQL Server driver).
     - A SQL Server instance to test against. A local Docker instance is fine; SQL Edge or full SQL Server both work.
     - `uv sync` for the preprocessor.
  4. **H2 "Setup — 5 steps"**:
     1. Build the custom K6 binary: `./k6-loadtest/build_k6.sh`. Explain what it does (`xk6 build --with github.com/grafana/xk6-sql --with github.com/grafana/xk6-sql-driver-sqlserver`).
     2. Copy the sample config: `cp k6-loadtest/config.sample.yaml k6-loadtest/config.yaml`. Point at the reference for the exhaustive schema.
     3. Edit `k6-loadtest/config.yaml` — pick sources, targets, VUs, duration.
     4. Preprocess: `uv run k6-preprocess --config k6-loadtest/config.yaml --output k6-loadtest/output/`. Explain what gets written (schema/, chunks/, test.js, manifest.json).
     5. Apply the CREATE TABLE DDL: show the SQL block from `k6-loadtest/output/schema/`, note that you can pipe it to `sqlcmd` or paste in Azure Data Studio.
     6. Run K6: `MSSQL_PASSWORD=yourpass ./k6-loadtest/k6 run k6-loadtest/output/test.js`.
  5. **H2 "Source modes"** — decision guide:
     - **`mode: parquet`**: reads real data from parquet (typically `raw/` from the downloader, or `raw-normalized/` from normalize). Real ratios, real cardinalities. Slower startup (K6 preprocess reads and chunks the parquet).
     - **`mode: synthetic`**: K6 generates rows at runtime from column value ranges you declare in config.yaml. Instant startup, unlimited scale, no parquet needed. Good for first-time smoke tests and unbounded-scale runs.
     - Include a "when to use which" bullet list.
  6. **H2 "Sample local SQL Server (Docker)"**: a working `docker-compose.yml` snippet:
     ```yaml
     services:
       sqlserver:
         image: mcr.microsoft.com/mssql/server:2022-latest
         environment:
           MSSQL_SA_PASSWORD: "YourStrong@Passw0rd"
           ACCEPT_EULA: "Y"
         ports:
           - "1433:1433"
         volumes:
           - sqlserver-data:/var/opt/mssql
     volumes:
       sqlserver-data:
     ```
     With a matching config.yaml host/user/password stub.
  7. **H2 "Config reference (summary)"**: name every top-level key in `config.sample.yaml` with a one-line description. Point at Reference→Configuration for the exhaustive schema.
  8. **H2 "Interpreting K6 output"** — annotated snippet of real K6 output:
     - The K6 summary block: `http_reqs`, `iteration_duration`, `vus`, `data_received`, etc.
     - The xk6-sql-specific metrics: `sql_query_duration`, `sql_rows_affected`.
     - What "acceptable" looks like for a bulk-insert scenario vs a query-latency scenario.
     - A brief mention of Prometheus/InfluxDB export via `--out` for long-running tests.

- [ ] **6.2 Shrink `k6-loadtest/README.md`**

  Replace with:
  ```markdown
  # k6-loadtest

  K6-based SQL Server load tester with a Python preprocessor. Turns parquet (or synthetic data specs) into a K6 test bundle: CREATE TABLE DDL, chunked JSON payloads, a K6 `test.js`, and a manifest.

  → **[Full guide](https://andrekamman.github.io/taxi/guides/k6-loadtest/)**
  ```

- [ ] **6.3 Update `mkdocs.yml` nav**
  ```yaml
        - K6 Load Test: guides/k6-loadtest.md
  ```

- [ ] **6.4 Verify build.**
- [ ] **6.5 Commit**
  ```bash
  git add docs/guides/k6-loadtest.md k6-loadtest/README.md mkdocs.yml
  git commit -m "docs: k6-loadtest guide + shrink component README"
  ```

---

## Task 7: Cookbook

**Files:**
- Create: `docs/cookbook.md`
- Modify: `mkdocs.yml`

### Steps

- [ ] **7.1 Create `docs/cookbook.md`**

  Length target: 300–400 lines (six recipes, each 40–60 lines).

  Header paragraph explaining what the cookbook is (scenario-oriented, not tool-oriented — cross-cutting recipes for real-world use).

  Six recipes, each with **H2 heading**, **Goal**, **Recipe** (numbered commands), **Notes**:

  1. **Nightly refresh via cron** — the incremental catch-up scenario. Show:
     - The command to run: `./downloader/download_taxi_data.sh --recent 3 && uv run normalize && uv run schema-drift --output /var/log/taxi/drift-$(date +%Y%m%d).txt`.
     - A `crontab` entry: `0 4 * * * cd /srv/taxi && ./bin/nightly-refresh.sh >> /var/log/taxi/refresh.log 2>&1`.
     - Alternative: systemd timer + service file snippets.
     - Note: `--recent`'s stop-on-local semantics makes this idempotent.
  2. **DuckDB `httpfs` — no local mirror** — when you don't need to download. Show a full working DuckDB session that installs httpfs, LOADS it, and runs a real query against `read_parquet('https://d37ci6vzurychx.cloudfront.net/trip-data/yellow_tripdata_2024-01.parquet')` to compute average trip distance. Discuss caching, cost of repeated queries.
  3. **Side-by-side comparison of multiple TLC years** — a DuckDB query that unions yellow across years post-normalization, computes trips-per-year, average fare, top pickup locations. Full copy-pasteable SQL.
  4. **Load-testing the normalizer's output** — chaining normalize → k6-preprocess against `raw-normalized/` instead of `raw/`. Explain config changes and why load-testing normalized data is more representative of your production data path.
  5. **Running behind a corporate proxy** — env vars for curl (`HTTPS_PROXY`, `NO_PROXY`), uv (uses these too), `gh` (`--proxy` flag). A working example that assumes a proxy at `http://proxy.corp.internal:3128`.
  6. **Populating a fresh dev SQL Server** — the "get me from zero to a filled SQL Server" pipeline. Docker-compose (link back to k6-loadtest guide for the compose file), download → normalize → k6-preprocess in parquet mode, apply DDL, run the load test as a one-shot bulk load.

- [ ] **7.2 Update `mkdocs.yml` nav**
  ```yaml
    - Cookbook: cookbook.md
  ```
  (place after Guides, before Architecture)

- [ ] **7.3 Verify build.**
- [ ] **7.4 Commit**
  ```bash
  git add docs/cookbook.md mkdocs.yml
  git commit -m "docs: cookbook (6 cross-cutting recipes)"
  ```

---

## Task 8: Architecture

**Files:**
- Create: `docs/architecture.md`
- Modify: `mkdocs.yml`

### Steps

- [ ] **8.1 Create `docs/architecture.md`**

  Length target: 200–260 lines. Structure:

  1. **H1**: `Architecture`
  2. **H2 "Repo layout"** — tree diagram of the monorepo, with 1-line descriptions per directory. Include: `downloader/`, `schema-drift/src/`, `normalize/src/`, `k6-loadtest/src/`, `shared/src/`, `tests/`, `docs/`. Note that the src-layout is used consistently for Python packages.
  3. **H2 "The four-tool DAG"** — mermaid flowchart:
     ````
     ```mermaid
     flowchart LR
       DL[downloader<br/>bash + curl] --> RAW[raw/<br/>parquet mirror]
       RAW --> SD[schema-drift<br/>DuckDB analysis]
       RAW --> NORM[normalize<br/>DuckDB transform]
       NORM --> RN[raw-normalized/<br/>uniform parquet]
       RN --> K6[k6-preprocess<br/>Python + DuckDB]
       K6 --> BUNDLE[K6 test bundle<br/>DDL + chunks + test.js]
       BUNDLE --> LOAD[K6 binary<br/>load test]
       LOAD --> SQL[(SQL Server)]
     ```
     ````
  4. **H2 "Core design principles"** — each is a subsection with a paragraph explaining the "why":
     - **WAF-aware retry** (downloader): why classifying 403 correctly is a first-class concern, given CloudFront's ambiguous 403 response for missing-vs-blocked.
     - **Data loss is an error** (normalize): why `ack_date` is required — force the human to think about it once.
     - **Per-file atomicity** (normalize, downloader): `.tmp.parquet` → atomic rename; interrupted runs leave no half-written files.
     - **Metadata-first, scan only when needed** (normalize): parquet footer for schema/null/range checks; only precision truncation triggers a full column scan.
     - **Monorepo rationale**: one shared package (`taxi_shared`) for DuckDB→SQL Server type mapping and CREATE TABLE generation; single install; simpler ops than three coordinated repos.
  5. **H2 "`taxi_shared` — the shared library"** — what's in it (type mapping, SQL generator), who imports it (k6-loadtest today; SQL Server loader tomorrow when that sub-project ships).
  6. **H2 "Testing philosophy"** — synthetic parquet fixtures built by DuckDB in `conftest.py`; no network dependencies, no shared filesystem state. Each test builds its own fixtures under `tmp_path`. Result: 83 tests in <1s.
  7. **H2 "What's not built yet"** — the SQL Server loader, orchestrator, dev/test/prod promotion. Link to each planned sub-project's spec if committed.

- [ ] **8.2 Update `mkdocs.yml` nav**
  ```yaml
    - Architecture: architecture.md
  ```

- [ ] **8.3 Verify build.**
- [ ] **8.4 Commit**
  ```bash
  git add docs/architecture.md mkdocs.yml
  git commit -m "docs: architecture overview"
  ```

---

## Task 9: Reference (Configuration + Exit codes)

**Files:**
- Create: `docs/reference/configuration.md`
- Create: `docs/reference/exit-codes.md`
- Modify: `mkdocs.yml`

### Steps

- [ ] **9.1 Create `docs/reference/configuration.md`**

  Length target: 200–280 lines. Reference-grade — every field, no editorializing. Structure:

  1. **H1**: `Configuration reference`
  2. **H2 "Normalize mapping YAML"** — every field of `normalize/mappings/<type>.yaml`:
     - `target:` — required string; a filename in `raw/<type>/**/`. Pins the "latest schema" file the normalizer conforms to.
     - `renames:` — optional dict of `<old_name>: <new_name>`. Both must be strings.
     - `lossy_casts:` — optional dict of `<column>: { from: TYPE, to: TYPE, ack_date: YYYY-MM-DD, ack_by?: str, reason?: str }`. `ack_date` required; others optional.
     - `acknowledged_data_loss:` — optional dict of `<column>: { ack_date: YYYY-MM-DD, ack_by?: str, reason?: str }`. `ack_date` required.
     - Section on "automatic behavior" — what gets applied without any mapping entry (safe widening, all-null drop, null-fill).
     - Section on "what triggers an error" (unmapped drop of non-null column, unacked lossy cast).
  3. **H2 "K6 load test config YAML"** — every field of `k6-loadtest/config.yaml`:
     - `data_sources:` (per-source dict):
       - `mode:` — `parquet` | `synthetic`.
       - `key_columns:` — list of column names.
       - Parquet-mode fields: `path:`, `filter:` (optional SQL WHERE), `max_rows:` (optional int).
       - Synthetic-mode fields: `columns:` (dict of `<col>: { type, min, max, ... }`). Type-specific rules.
     - `sql_servers:` (list of dicts): `name`, `host`, `port`, `database`, `user`, `password_env`.
     - `scenarios:` (list of dicts): `name`, `data_source`, `target`, `vus`, `duration`, `iterations`.
  4. **H2 "Environment variables"**:
     - `OUTPUT_DIR` (downloader) — override the default `raw/` directory. Absolute or relative to CWD.
     - `MSSQL_PASSWORD` (k6 test.js) — SQL Server password.
     - `HTTPS_PROXY`, `NO_PROXY` — standard curl/uv/gh behavior; the downloader inherits.

- [ ] **9.2 Create `docs/reference/exit-codes.md`**

  Length target: 80–120 lines. One table per tool:

  1. **H1**: `Exit codes`
  2. **H2 "Downloader (`download_taxi_data.sh`)"**:
     | Code | Meaning | Suggested action |
     |---|---|---|
     | 0 | All requested downloads succeeded (or were already present) | — |
     | 1 | Persistent rate-limit / WAF block after 3 backoff attempts (5→15→60 min) | Wait several hours before retrying; check for IP-level flagging |
     | 2 | Argument error (unknown flag, invalid TYPE) | Run with `--help` for usage |
     | Ctrl-C (130) | User interrupted | — |
  3. **H2 "Normalize (`uv run normalize`)"**:
     | Code | Meaning | Suggested action |
     |---|---|---|
     | 0 | Success; all files normalized or already present | — |
     | 1 | Mapping incomplete; unresolved items reported and mapping amended | Edit the mapping YAML, re-run |
     | 2 | Configuration error (missing raw data, malformed mapping, target not found) | Fix the reported issue |
     | 3 | First run; scaffold generated | Review the scaffold, edit, re-run |
  4. **H2 "K6-preprocess (`uv run k6-preprocess`)"** — enumerate from the source. Grep `k6-loadtest/src/k6_loadtest/preprocess.py` and `k6-loadtest/src/k6_loadtest/cli_entry.py` (or wherever `sys.exit(...)` is called) for exit calls; also check `argparse` error paths (which exit 2 by default). Expected result: at minimum 0 (success), 1 (validation error), 2 (argument error). Verify by running `uv run k6-preprocess` with various bad inputs.
  5. **H2 "Schema-drift (`uv run schema-drift`)"** — same approach. Grep `schema-drift/src/schema_drift/cli.py` for exit calls. Expected: 0 (success), 1 (data-dir missing), 2 (argparse). Verify with `uv run schema-drift --data-dir /nonexistent` for the error case.

- [ ] **9.3 Update `mkdocs.yml` nav**
  ```yaml
    - Reference:
        - Configuration: reference/configuration.md
        - Exit codes: reference/exit-codes.md
  ```

- [ ] **9.4 Verify build.**
- [ ] **9.5 Commit**
  ```bash
  git add docs/reference/configuration.md docs/reference/exit-codes.md mkdocs.yml
  git commit -m "docs: reference (configuration + exit codes)"
  ```

---

## Task 10: Contributing + top-level README rewrite + Design Specs surface

**Files:**
- Create: `docs/contributing.md`
- Modify: `README.md` (top-level, rewrite)
- Modify: `mkdocs.yml` (add Design Specs section referencing existing files)

### Steps

- [ ] **10.1 Create `docs/contributing.md`**

  Length target: 130–180 lines. Structure:

  1. **H1**: `Contributing`
  2. **H2 "Dev setup"**:
     ```bash
     git clone https://github.com/andrekamman/taxi.git
     cd taxi
     uv sync --extra test --extra docs
     ```
     Explain `--extra test` (pytest) and `--extra docs` (mkdocs + material + extensions).
  3. **H2 "Running the test suite"**:
     - Full suite: `uv run --extra test pytest -q`.
     - Single tool: `uv run --extra test pytest tests/taxi_normalize -v`.
     - Single test: `uv run --extra test pytest tests/taxi_normalize/test_planner.py::test_pure_passthrough_identical_schemas -v`.
  4. **H2 "Adding a new test"** — link to `tests/taxi_normalize/conftest.py` as the fixture pattern reference; note that fixtures build synthetic parquet via DuckDB and each test gets a `tmp_path`.
  5. **H2 "Building the docs locally"**:
     ```bash
     uv run --extra docs mkdocs serve
     ```
     Open `http://127.0.0.1:8000/`. Live-reloads on edit. To do a strict build (fail on any warning), run `mkdocs build --strict`.
  6. **H2 "PR checklist"**:
     - Tests pass locally.
     - Docs pass `mkdocs build --strict` if you touched anything under `docs/` or any component README.
     - Commit messages follow the existing convention (`type(scope): imperative subject`).
     - If you changed user-facing behavior, update the relevant guide + reference page.
  7. **H2 "Code style"**:
     - Existing informal conventions: single-quote SQL literals via f-strings; explicit exit-code semantics; error-first paths in CLI; `ack_date` and other ISO-8601 strings never numeric.
     - No enforced formatter yet; PRs welcome to add ruff or black.
  8. **H2 "Where design decisions live"**:
     - `docs/superpowers/specs/` for each sub-project's design spec.
     - `docs/superpowers/plans/` for the associated implementation plans.
     - Commit history for the "why did this line change" questions.

- [ ] **10.2 Rewrite `README.md`** (top-level, at repo root)

  Length target: 70–100 lines. Concise GitHub landing page — hooks the reader and points at the site. Structure:

  1. **H1**: `taxi`
  2. **Badges row** (using shields.io):
     ```markdown
     [![CI](https://github.com/andrekamman/taxi/actions/workflows/ci.yml/badge.svg)](https://github.com/andrekamman/taxi/actions/workflows/ci.yml)
     [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
     [![Docs](https://img.shields.io/badge/docs-online-brightgreen)](https://andrekamman.github.io/taxi/)
     ```
  3. **Hero paragraph (2–3 sentences)**.
  4. **Three-bullet differentiators** — same as the site's Home page, condensed to 1 line each.
  5. **"→ Full documentation at https://andrekamman.github.io/taxi/"** as a prominent line.
  6. **Quick Start** (same three commands as Home).
  7. **Requirements** (bullet list, brief).
  8. **Acknowledgments** (Todd Schneider link, THIRD_PARTY_NOTICES link).

  Explicitly does NOT duplicate the site's content. The site is the source of truth; the README is the hook.

- [ ] **10.3 Add Design Specs to `mkdocs.yml` nav**

  Final `nav:` block:
  ```yaml
  nav:
    - Home: index.md
    - Getting Started: getting-started.md
    - Guides:
        - Downloader: guides/downloader.md
        - Schema Drift: guides/schema-drift.md
        - Normalize: guides/normalize.md
        - K6 Load Test: guides/k6-loadtest.md
    - Cookbook: cookbook.md
    - Architecture: architecture.md
    - Reference:
        - Configuration: reference/configuration.md
        - Exit codes: reference/exit-codes.md
    - Design Specs:
        - Monorepo restructure: superpowers/specs/2026-07-19-monorepo-restructure-design.md
        - Normalizer: superpowers/specs/2026-07-21-normalizer-design.md
        - Documentation site: superpowers/specs/2026-07-22-documentation-design.md
    - Contributing: contributing.md
  ```

  (If a spec file is not present in the public repo, either import it via `cp` from taxi-dev and commit as a preliminary step, or omit its nav entry — MkDocs will fail the strict build otherwise.)

- [ ] **10.4 Verify build.**

- [ ] **10.5 Commit**
  ```bash
  git add docs/contributing.md README.md mkdocs.yml
  git commit -m "docs: contributing guide + README rewrite as landing page + Design Specs nav"
  ```

---

## Post-implementation verification

- [ ] **Full test suite green:**
  ```bash
  uv run --extra test pytest -q
  ```
  Expected: 83 passed.

- [ ] **Docs build strict:**
  ```bash
  uv run --extra docs mkdocs build --strict
  ```
  Expected: `Documentation built` with no warnings.

- [ ] **Site is live** at `https://andrekamman.github.io/taxi/` and shows the full navigation. Each page loads without 404s. Internal links resolve.

- [ ] **All per-component READMEs are ≤ 20 lines** and point at their guide:
  ```bash
  wc -l downloader/README.md schema-drift/README.md normalize/README.md k6-loadtest/README.md
  ```

- [ ] **Top-level README is ≤ 100 lines** and links to the site:
  ```bash
  wc -l README.md
  grep -q 'andrekamman.github.io/taxi' README.md && echo "README links to site"
  ```

- [ ] **Downloader change works end-to-end** (fresh checkout, `--recent 3 yellow` behaves per Task 0.10).

- [ ] **All 11 commits present on `main`** in order:
  ```bash
  git log --oneline origin/main..HEAD
  ```

- [ ] **CI workflow succeeded** for the final push: `gh run list --workflow=ci.yml --limit 3`.

- [ ] **GitHub Pages homepage is set:**
  ```bash
  gh repo view andrekamman/taxi --json homepageUrl -q .homepageUrl
  ```
  Expected: `https://andrekamman.github.io/taxi/`.
