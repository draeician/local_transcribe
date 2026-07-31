# Quick Reference Guide

Queue-first workflow (0.5.0+). Producers enqueue; one NLM-locked worker downloads and transcribes.

## Most common commands

### First-time setup (worker host)

```bash
pipx install . --force   # or: pipx install git+https://github.com/draeician/local_transcribe.git
lt doctor

lt queue init --queue-dir /path/to/transcription-queue
lt queue doctor
lt worker install
systemctl --user daemon-reload
systemctl --user enable --now local-transcribe-worker.service
```

Add cookies for YouTube downloads in `~/.config/local-transcribe/config.yaml`:

```yaml
queue:
  default_auth_profile: yt
auth_profiles:
  yt:
    cookies_file: ~/.config/local-transcribe/youtube-cookies.txt
```

Then: `systemctl --user restart local-transcribe-worker.service`

### Everyday producers

```bash
# Enqueue one URL (waits for completion by default)
lt transcribe "https://www.youtube.com/watch?v=VIDEO_ID"

# Enqueue and return immediately
lt transcribe "https://www.youtube.com/watch?v=VIDEO_ID" --no-wait

# Local audio file
lt transcribe ./recording.m4a --output-dir ./out

# Bulk enqueue
lt batch --input inputfile.txt

# Explicit queue add
lt queue add "https://www.youtube.com/watch?v=VIDEO_ID" --origin cli --priority 10
```

### Watch / inspect the queue

```bash
lt queue stats                         # counts by state (use this to see backlog shrink)
lt queue list                          # pending (default, first 50)
lt queue list --status processing
lt queue list --status completed
lt queue list --status failed --limit 100
lt queue show <execution_id>
lt queue show-source youtube:VIDEO_ID
```

### Worker ops

```bash
lt worker status
systemctl --user status local-transcribe-worker.service
journalctl --user -u local-transcribe-worker.service -f
lt worker logs
lt worker restart
```

### Import / repair / retry

```bash
lt queue import ~/references/transcripts/transcript-pending.md
lt queue retry --source youtube:VIDEO_ID
lt queue cancel <execution_id>         # pending only
lt queue purge --status failed         # never deletes transcript JSON
lt queue export-pending
```

### Emergency: bypass queue (legacy in-process)

```bash
lt transcribe "https://www.youtube.com/watch?v=VIDEO_ID" --direct --cookies-file ~/cookies.txt
lt batch --input inputfile.txt --direct --resume
lt status
lt reconcile
lt report
```

---

## Typical workflows

### Workflow 1: Fresh queue + worker

```bash
lt queue init --queue-dir /opt/.../transcription-queue
lt worker install && systemctl --user enable --now local-transcribe-worker.service
lt batch --input inputfile.txt
lt queue stats
```

### Workflow 2: Migrate legacy pending list

```bash
lt queue import ~/references/transcripts/transcript-pending.md
lt queue stats
journalctl --user -u local-transcribe-worker.service -f
```

### Workflow 3: Single interactive capture

```bash
lt transcribe "$URL"          # enqueue + wait
# or from another host that shares the same queue.path / UUID
lt transcribe "$URL" --no-wait
```

### Workflow 4: Retry a failed source

```bash
lt queue list --status failed
lt queue retry --source youtube:VIDEO_ID
```

### Workflow 5: Offline / no worker available

```bash
lt transcribe "$URL" --direct --cookies-file ~/.config/local-transcribe/youtube-cookies.txt
```

---

## Common options

```bash
# Models / devices (stored on the execution; worker applies them)
lt batch --input INPUT.txt --model medium --device cuda --compute-type float16
lt batch --input INPUT.txt --model large --device cuda
lt batch --input INPUT.txt --device cpu --compute-type int8
lt batch --input INPUT.txt --max-retries 5
lt batch --input INPUT.txt --wait --timeout 3600

# Force a new generation for an already-completed source
lt transcribe "$URL" --force

# Queue path override (normally use config.yaml)
lt queue stats --queue-dir /path/to/transcription-queue
```

---

## Layout (queue on NFS)

```
transcription-queue/
├── queue.id
├── pending/           # waiting for worker
├── processing/        # claimed by lock holder
├── completed/
├── failed/
├── retry/
├── cancelled/
├── keys/              # source reservations
├── worker/
│   ├── worker.lock    # NLM exclusive ownership
│   └── state.json     # diagnostic heartbeat only
└── tmp/
```

Local (not on NFS):

```
~/.config/local-transcribe/config.yaml
~/.config/local-transcribe/youtube-cookies.txt
~/.local/state/local-transcribe/logs/
~/.cache/local-transcribe/jobs/<execution-id>/
```

Transcript JSON still lands under your configured transcripts root (e.g. `~/references/transcripts` or shared NFS path).

---

## Troubleshooting

### List looks stuck but transcripts appear
Use `lt queue stats`. `lt queue list` only shows pending by default.

### Worker not draining
```bash
systemctl --user status local-transcribe-worker.service
lt worker status
lt queue doctor
```

### HTTP 403 / RateLimitError on downloads
Export Netscape YouTube cookies, set `default_auth_profile`, restart worker. API keys do not help yt-dlp.

### Second host also tries to work
Only one NLM lock holder runs jobs. Other hosts should only enqueue (`lt transcribe` / `lt batch` / `lt queue add`).

### Need legacy batch ledger tools
```bash
lt reconcile
lt status          # batch_status.json (direct mode)
lt report
```

---

## Important files

| Path | Purpose | Safe to delete? |
|------|---------|-----------------|
| `queue/pending|processing|…` | Job records | ⚠️ only via `lt queue purge` for terminal states |
| `queue/keys/` | Source reservations | ⚠️ NO |
| `queue/worker/worker.lock` | Ownership | ⚠️ NEVER replace while held |
| `~/.config/local-transcribe/config.yaml` | Queue path + auth | ⚠️ keep |
| Transcript `*.json` | Your data | ⚠️ NO |
| `batch_status.json` | Legacy direct-mode resume | only if unused |

---

## Quick start checklist

- [ ] `pipx install . --force` / `lt doctor`
- [ ] `lt queue init` + verify `config.yaml`
- [ ] Cookies / `default_auth_profile` for YouTube
- [ ] `lt worker install` + enable systemd user unit
- [ ] `lt batch --input …` or `lt queue import …`
- [ ] Monitor with `lt queue stats` (not only `lt queue list`)
- [ ] Emergency escape hatch: `--direct`

---

## Full documentation

- **START_HERE.md** — install + first run
- **README.md** — setup & YouTube/CUDA troubleshooting
- **docs/QUEUE_OPERATOR.md** — NFS mounts, systemd, security
- **docs/QUEUE_NFS_LAB.md** — multi-host lab
- **docs/REF_CLI_QUEUE_ADAPTER.md** — ref-cli enqueue API
- **CHANGELOG.md** — 0.5.0 release notes
- `lt --help` / `lt queue --help` / `lt worker --help`
