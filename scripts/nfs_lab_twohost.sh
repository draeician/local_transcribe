#!/usr/bin/env bash
# Two-host NFSv3 NLM + enqueue lab (task 036).
set -u
LAB="${LT_NFS_QUEUE_DIR:-/opt/md1/git/tmp/local-transcribe-queue-lab}"
PROBE="$LAB/nlm_probe.py"
EVID="${1:-/tmp/lt_nfs_036_matrix.txt}"
REMOTE_A="${REMOTE_A:-draeician@192.168.22.52}" # virindi
REMOTE_B="${REMOTE_B:-draeician@192.168.22.60}" # localai
REPO="${REPO:-/home/draeician/git/personal/local_transcribe}"

log() { echo "[$(date -Is)] $*" | tee -a "$EVID"; }

mkdir -p "$LAB"
if [[ ! -f "$PROBE" ]]; then
  echo "missing $PROBE" >&2
  exit 1
fi

: >"$EVID"
log "lab=$LAB"
log "clients: local=$(hostname) A=$REMOTE_A B=$REMOTE_B"

# Cleanup stray probes
pkill -9 -f "$PROBE" 2>/dev/null || true
ssh -o BatchMode=yes -o ConnectTimeout=10 "$REMOTE_A" "pkill -9 -f nlm_probe.py || true" || true
ssh -o BatchMode=yes -o ConnectTimeout=10 "$REMOTE_B" "pkill -9 -f nlm_probe.py || true" || true
sleep 1

# --- A: contention + clean exit ---
Q="$LAB/matrix-a-$(date +%H%M%S)"
mkdir -p "$Q/worker"
LOCK="$Q/worker/worker.lock"
log "A_lock=$LOCK"
ssh -o BatchMode=yes "$REMOTE_A" "python3 '$PROBE' '$LOCK' hold 12" >/tmp/a_hold.out 2>/tmp/a_hold.err &
AP=$!
for _ in $(seq 1 100); do grep -q HELD /tmp/a_hold.out 2>/dev/null && break; sleep 0.1; done
log "A_holder=$(tr '\n' ' ' </tmp/a_hold.out)"
log "A_local_try=$(python3 "$PROBE" "$LOCK" try)"
log "A_B_try=$(ssh -o BatchMode=yes "$REMOTE_B" "python3 '$PROBE' '$LOCK' try" || echo ERR)"
wait "$AP" || true
sleep 1
log "A_local_after=$(python3 "$PROBE" "$LOCK" try)"
log "A_A_after=$(ssh -o BatchMode=yes "$REMOTE_A" "python3 '$PROBE' '$LOCK' try" || echo ERR)"
log "A_B_after=$(ssh -o BatchMode=yes "$REMOTE_B" "python3 '$PROBE' '$LOCK' try" || echo ERR)"

# --- B: kill -9 reclaim (PID file on NFS) ---
Q="$LAB/matrix-b-$(date +%H%M%S)"
mkdir -p "$Q/worker"
LOCK="$Q/worker/worker.lock"
PIDF="$Q/worker/holder.pid"
log "B_lock=$LOCK"
ssh -o BatchMode=yes "$REMOTE_A" "python3 - <<'PY'
import errno, fcntl, json, os, socket, time
from datetime import datetime, timezone
from pathlib import Path
path = Path('$LOCK')
pidf = Path('$PIDF')
path.parent.mkdir(parents=True, exist_ok=True)
fh = path.open('a+', encoding='utf-8')
try:
    fcntl.lockf(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError as exc:
    raise SystemExit(f'acquire_failed:{exc}')
pidf.write_text(str(os.getpid()) + '\n', encoding='utf-8')
meta = {
    'hostname': socket.gethostname(),
    'pid': os.getpid(),
    'acquired_at': datetime.now(timezone.utc).isoformat(),
}
fh.seek(0)
fh.truncate()
json.dump(meta, fh, indent=2)
fh.write('\n')
fh.flush()
os.fsync(fh.fileno())
print('HELD', flush=True)
while True:
    time.sleep(3600)
PY" >/tmp/b_hold.out 2>/tmp/b_hold.err &
BP=$!
for _ in $(seq 1 100); do grep -q HELD /tmp/b_hold.out 2>/dev/null && break; sleep 0.1; done
log "B_holder=$(tr '\n' ' ' </tmp/b_hold.out) pid=$(cat "$PIDF" 2>/dev/null || echo missing)"
log "B_local_try=$(python3 "$PROBE" "$LOCK" try)"
RPID="$(tr -d '[:space:]' <"$PIDF" 2>/dev/null || true)"
if [[ -n "${RPID:-}" ]]; then
  ssh -o BatchMode=yes "$REMOTE_A" "kill -9 $RPID; echo kill_rc:\$?" | tee -a "$EVID"
else
  log "B_ERROR no pid file"
fi
kill "$BP" 2>/dev/null || true
START=$(date +%s)
RECLAIM=FAIL
for _ in $(seq 1 180); do
  GOT=$(python3 "$PROBE" "$LOCK" try)
  if [[ "$GOT" == OK ]]; then
    RECLAIM=OK
    log "B_reclaim=OK elapsed=$(( $(date +%s) - START ))s"
    break
  fi
  sleep 1
done
[[ "$RECLAIM" == OK ]] || log "B_reclaim=FAIL"

# --- C: cross-host enqueue ---
PYSRC="$LAB/py_src"
rm -rf "$PYSRC"
mkdir -p "$PYSRC"
cp -r "$REPO/src/local_transcribe" "$PYSRC/"
Q="$LAB/matrix-enq-$(date +%H%M%S)"
PYTHONPATH="$REPO/src" python3 - <<PY
from pathlib import Path
from local_transcribe.services.queue_paths import initialize_queue_layout
initialize_queue_layout(Path("$Q"))
print("$Q")
PY
URL='https://youtu.be/twoHostEnq1'
(
  PYTHONPATH="$PYSRC" python3 -c "from pathlib import Path; from local_transcribe.services.queue_store import QueueStore; print(QueueStore(Path('$Q')).enqueue('$URL', origin='nomnom').kind)"
) >/tmp/e1.out 2>/tmp/e1.err &
(
  ssh -o BatchMode=yes "$REMOTE_A" "PYTHONPATH='$PYSRC' python3 -c \"from pathlib import Path; from local_transcribe.services.queue_store import QueueStore; print(QueueStore(Path('$Q')).enqueue('$URL', origin='virindi').kind)\""
) >/tmp/e2.out 2>/tmp/e2.err &
(
  ssh -o BatchMode=yes "$REMOTE_B" "PYTHONPATH='$PYSRC' python3 -c \"from pathlib import Path; from local_transcribe.services.queue_store import QueueStore; print(QueueStore(Path('$Q')).enqueue('$URL', origin='localai').kind)\""
) >/tmp/e3.out 2>/tmp/e3.err &
wait || true
log "C_nomnom=$(cat /tmp/e1.out 2>/dev/null) err=$(head -c 160 /tmp/e1.err 2>/dev/null)"
log "C_virindi=$(cat /tmp/e2.out 2>/dev/null) err=$(head -c 160 /tmp/e2.err 2>/dev/null)"
log "C_localai=$(cat /tmp/e3.out 2>/dev/null) err=$(head -c 160 /tmp/e3.err 2>/dev/null)"
log "C_pending=$(ls "$Q/pending" 2>/dev/null | wc -l)"
log "C_keys=$(ls "$Q/keys" 2>/dev/null | wc -l)"

log "DONE"
cat "$EVID"
