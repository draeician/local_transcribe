# ref-cli queue adapter contract (task 026)

`local-transcribe` exposes a Python enqueue API for `ref-cli`:

```python
from local_transcribe.queue_api import enqueue_youtube

try:
    result = enqueue_youtube(url, origin="ref", priority=20)
except Exception:
    # Fall back to transcript-pending.md — ref capture must not fail
    append_pending(url)
else:
    # result.kind in enqueued | existing_active | already_completed | requires_force
    pass
```

CLI equivalent:

```bash
lt queue add "$URL" --origin ref --priority 20
```

**Ownership:** `ref-cli` updates `references.md` / reconcile; `local-transcribe` never rewrites `references.md`.

**Minimum version:** queue API available from the `feat/queue-creation` line (post task 026).

Remaining work on the **ref-cli** repository (not this repo): wire the try/except adapter and `ref reconcile-transcripts`.
