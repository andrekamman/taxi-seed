# orchestrator

Runs the pipeline as one command: `taxi-run [TYPE]` chains download → normalize
→ (opt-in `--load`) load, honoring each stage's exit codes and halting a type
when normalize needs human review. `taxi-curate-mappings` auto-accepts detected
schema drift into complete mapping YAMLs and writes an audit report.

Run it as a console script or a module:

```bash
taxi-run                       # download + normalize, all four types
taxi-run yellow --recent 3 --load   # recent-mode, then load into SQL Server
python -m taxi_orchestrate.cli yellow --dry-run
```

→ **[Full guide](https://andrekamman.github.io/taxi-seed/guides/orchestrator/)**
