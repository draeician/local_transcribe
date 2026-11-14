"""Subprocess helper for NFSv3 NLM lock lab tests."""

from __future__ import annotations

import sys
import time
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("usage: nfs_lock_helper.py <lock-path> hold|try|hold-until-signal ...", file=sys.stderr)
        return 2
    lock_path = Path(argv[1])
    mode = argv[2]

    # Ensure src is importable when launched outside pytest.
    repo_src = Path(__file__).resolve().parents[1] / "src"
    if str(repo_src) not in sys.path:
        sys.path.insert(0, str(repo_src))

    from local_transcribe.services.worker_lock import WorkerAlreadyActive, WorkerLock

    if mode == "hold":
        seconds = float(argv[3]) if len(argv) > 3 else 30.0
        lock = WorkerLock.acquire(lock_path)
        print("HELD", flush=True)
        time.sleep(seconds)
        lock.release()
        print("RELEASED", flush=True)
        return 0

    if mode == "hold-kill":
        # Hold until killed; parent sends SIGKILL.
        lock = WorkerLock.acquire(lock_path)
        print("HELD", flush=True)
        while True:
            time.sleep(3600)

    if mode == "try":
        try:
            lock = WorkerLock.acquire(lock_path)
        except WorkerAlreadyActive:
            print("BUSY", flush=True)
            return 0
        lock.release()
        print("OK", flush=True)
        return 0

    if mode == "claim-hold":
        # argv: helper <unused-lock-path> claim-hold <queue-dir> <execution-id>
        if len(argv) < 5:
            print("usage: claim-hold <queue> <execution-id>", file=sys.stderr)
            return 2
        queue = Path(argv[3])
        execution_id = argv[4]
        import json

        from local_transcribe.services.queue_models import Execution
        from local_transcribe.services.worker import claim_execution

        pending = queue / "pending" / f"{execution_id}.json"
        execution = Execution.from_dict(json.loads(pending.read_text(encoding="utf-8")))
        claimed = claim_execution(
            queue, execution, hold_claim_lock=True, worker_slot=0
        )
        if claimed is None:
            print("NONE", flush=True)
            return 0
        print("CLAIMED", flush=True)
        while True:
            time.sleep(3600)

    if mode == "claim-try":
        if len(argv) < 5:
            print("usage: claim-try <queue> <execution-id>", file=sys.stderr)
            return 2
        queue = Path(argv[3])
        execution_id = argv[4]
        from local_transcribe.services.worker_slots import take_claim_lock

        try:
            take_claim_lock(queue, execution_id)
        except WorkerAlreadyActive:
            print("BUSY", flush=True)
            return 0
        from local_transcribe.services.worker_slots import release_claim

        release_claim(execution_id)
        print("OK", flush=True)
        return 0

    print(f"unknown mode {mode!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
