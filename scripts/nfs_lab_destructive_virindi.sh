#!/usr/bin/env bash
# Destructive NFSv3 lab on virindi using a *side* mount (does not remount /opt/md1).
# Run from nomnom: bash scripts/nfs_lab_destructive_virindi.sh
set -u
REMOTE="${REMOTE:-draeician@192.168.22.52}"
SIDE="${SIDE:-/mnt/lt-nfs-lab-036}"
EXPORT="${EXPORT:-media.draeician.com:/mnt/tank/md1}"
EVID="${1:-/tmp/lt_nfs_036_destructive.txt}"
REPO_SRC_ON_NFS="/opt/md1/git/tmp/local-transcribe-queue-lab/py_src"

log() { echo "[$(date -Is)] $*" | tee -a "$EVID"; }
: >"$EVID"

ssh -o BatchMode=yes "$REMOTE" "sudo -n true" || {
  echo "passwordless sudo required on $REMOTE" >&2
  exit 1
}

log "remote=$REMOTE side=$SIDE export=$EXPORT"

# Ensure clean side mount
ssh -o BatchMode=yes "$REMOTE" "sudo -n umount -l '$SIDE' 2>/dev/null || true; sudo -n mkdir -p '$SIDE'"
ssh -o BatchMode=yes "$REMOTE" "sudo -n mount -t nfs -o vers=3,proto=tcp,hard,local_lock=none,rw '$EXPORT' '$SIDE'"
log "side_mount=$(ssh -o BatchMode=yes "$REMOTE" "findmnt -T '$SIDE' -o TARGET,SOURCE,FSTYPE,OPTIONS -n | tr -s ' '")"

LAB="$SIDE/git/tmp/local-transcribe-queue-lab/destructive-$(date +%H%M%S)"
ssh -o BatchMode=yes "$REMOTE" "sudo -n mkdir -p '$LAB' && sudo -n chown draeician:draeician '$LAB'"

# Init queue via NFS-visible package if present, else minimal dirs
ssh -o BatchMode=yes "$REMOTE" "PYTHONPATH='$REPO_SRC_ON_NFS' python3 - <<PY
from pathlib import Path
try:
    from local_transcribe.services.queue_paths import initialize_queue_layout
    initialize_queue_layout(Path('$LAB'))
    print('init_ok')
except Exception as exc:
    p = Path('$LAB')
    for d in ['pending','processing','completed','failed','cancelled','retry','keys','tmp','worker']:
        (p/d).mkdir(parents=True, exist_ok=True)
    (p/'queue.id').write_text('{\"queue_uuid\":\"lab\",\"created_at\":\"x\"}\\n')
    print('init_fallback', exc)
PY"
log "lab=$LAB"

# --- Read-only remount ---
ssh -o BatchMode=yes "$REMOTE" "sudo -n mount -o remount,ro '$SIDE'"
log "remount_ro=$(ssh -o BatchMode=yes "$REMOTE" "findmnt -T '$SIDE' -o OPTIONS -n")"
# Writes must fail
RO_WRITE=$(ssh -o BatchMode=yes "$REMOTE" "python3 - <<'PY'
from pathlib import Path
p = Path('$LAB') / 'pending' / 'ro-probe.json'
try:
    p.write_text('{\"x\":1}\\n')
    print('WRITE_OK_UNEXPECTED')
except OSError as exc:
    print(f'WRITE_DENIED:{exc.errno}')
PY")
log "ro_write=$RO_WRITE"
# Mount validation should reject RO
RO_DOC=$(ssh -o BatchMode=yes "$REMOTE" "PYTHONPATH='$REPO_SRC_ON_NFS' python3 - <<'PY'
from pathlib import Path
from local_transcribe.services.mount_validation import validate_queue_mount
r = validate_queue_mount(Path('$LAB'), validate_nfs=True, require_statd=True)
print('ok' if r.ok else 'fail', ';'.join(r.errors[:3]))
PY")
log "ro_doctor=$RO_DOC"

# Remount rw and recover
ssh -o BatchMode=yes "$REMOTE" "sudo -n mount -o remount,rw '$SIDE'"
log "remount_rw=$(ssh -o BatchMode=yes "$REMOTE" "findmnt -T '$SIDE' -o OPTIONS -n | tr -s ' '")"
RW_WRITE=$(ssh -o BatchMode=yes "$REMOTE" "python3 - <<'PY'
from pathlib import Path
p = Path('$LAB') / 'pending' / 'rw-probe.json'
p.write_text('{\"ok\":true}\\n')
print('WRITE_OK' if p.is_file() else 'MISSING')
PY")
log "rw_write=$RW_WRITE"

# --- Mount interrupt / restore ---
# Start a background writer holding a file open, lazy-umount side mount, remount, verify
ssh -o BatchMode=yes "$REMOTE" "python3 - <<'PY' >/tmp/lt_interrupt_writer.out 2>&1 &
import time
from pathlib import Path
p = Path('$LAB') / 'pending' / 'interrupt-probe.json'
with p.open('w', encoding='utf-8') as fh:
    fh.write('{\"phase\":\"open\"}\\n')
    fh.flush()
    print('OPEN', flush=True)
    time.sleep(20)
    try:
        fh.write('{\"phase\":\"after\"}\\n')
        fh.flush()
        print('WRITE_AFTER', flush=True)
    except OSError as exc:
        print(f'WRITE_AFTER_FAIL:{exc}', flush=True)
PY"
sleep 1
log "interrupt_writer=$(ssh -o BatchMode=yes "$REMOTE" "cat /tmp/lt_interrupt_writer.out 2>/dev/null | tr '\n' ' '")"
ssh -o BatchMode=yes "$REMOTE" "sudo -n umount -l '$SIDE'; echo umount_rc:\$?"
log "after_umount_findmnt=$(ssh -o BatchMode=yes "$REMOTE" "findmnt -T '$SIDE' 2>&1 | head -2 | tr '\n' ' '")"
# Restore
ssh -o BatchMode=yes "$REMOTE" "sudo -n mount -t nfs -o vers=3,proto=tcp,hard,local_lock=none,rw '$EXPORT' '$SIDE'"
log "restored_mount=$(ssh -o BatchMode=yes "$REMOTE" "findmnt -T '$SIDE' -o FSTYPE,OPTIONS -n | tr -s ' '")"
sleep 2
log "interrupt_writer_end=$(ssh -o BatchMode=yes "$REMOTE" "cat /tmp/lt_interrupt_writer.out 2>/dev/null | tr '\n' ' '")"
# Enqueue after restore
ENQ=$(ssh -o BatchMode=yes "$REMOTE" "PYTHONPATH='$REPO_SRC_ON_NFS' python3 - <<'PY'
from pathlib import Path
from local_transcribe.services.queue_store import QueueStore
from local_transcribe.services.queue_paths import initialize_queue_layout
q = Path('$LAB')
# ensure layout if umount wiped visibility weirdly
try:
    initialize_queue_layout(q)
except Exception:
    pass
r = QueueStore(q).enqueue('https://youtu.be/afterUmount01', origin='virindi-restore')
print(r.kind)
PY")
log "enqueue_after_restore=$ENQ"

# Cleanup side mount (leave /opt/md1 alone)
ssh -o BatchMode=yes "$REMOTE" "sudo -n umount -l '$SIDE' 2>/dev/null || true"
log "cleanup_umount_done"
log "DONE"
cat "$EVID"
