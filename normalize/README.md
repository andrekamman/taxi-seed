# normalize

Rewrites historical TLC parquet to conform to the latest schema. Data loss is a first-class error — halts unless every discarded column or lossy cast is explicitly acknowledged. Auto-bootstrap on first run; auto-amend on new drift.

→ **[Full guide](https://andrekamman.github.io/taxi-seed/guides/normalize/)**
