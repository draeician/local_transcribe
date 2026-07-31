# ref-cli queue adapter contract (task 026 / 035)

`local-transcribe` exposes a safe enqueue API for `ref-cli`:

```python
from pathlib import Path
from local_transcribe.queue_api import enqueue_youtube_safe

PENDING = Path.home() / "references" / "transcripts" / "transcript-pending.md"

outcome = enqueue_youtube_safe(
    url,
    origin="ref",
    priority=20,
    pending_fallback=PENDING,
)
if not outcome.ok:
    raise RuntimeError(outcome.error or "enqueue failed")
if outcome.fell_back_to_pending:
    # queue unavailable — capture still succeeded via pending file
    ...
else:
    # outcome.result.kind in enqueued | existing_active | already_completed | requires_force
    ...
```

Strict (raises) form:

```python
from local_transcribe.queue_api import enqueue_youtube

result = enqueue_youtube(url, origin="ref", priority=20)
```

CLI equivalent:

```bash
lt queue add "$URL" --origin ref --priority 20
```

## Ownership

- `ref-cli` updates `references.md` / reconcile; `local-transcribe` never rewrites `references.md`.
- Capture must not fail solely because the queue or NFS is unavailable — use `enqueue_youtube_safe` with `pending_fallback`.

## Cross-project fixture

In-repo simulation (no ref checkout required):

```python
# tests/test_queue_concurrency.py::test_enqueue_youtube_safe_fallback
# tests/test_queue_concurrency.py::test_enqueue_youtube_safe_success
```

These cover the adapter contract: success path and pending-file fallback when the queue is not configured.

## ref-cli wiring

`ref_cli.cli.add_url_to_pending_file` should:

1. Skip when a transcript already exists on disk.
2. Call `enqueue_youtube` when `local_transcribe` is importable.
3. On `ImportError` or any enqueue failure, append to `transcript-pending.md` (legacy path).

Minimum version: **local-transcribe 0.5.0** (queue API from tasks 026 / 035).
