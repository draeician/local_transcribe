#!/usr/bin/env python3
"""Minimal NLM lock probe for two-host NFS lab (stdlib only)."""

from __future__ import annotations

import errno
import fcntl
import json
import os
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


class Busy(Exception):
    pass


def acquire(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = path.open("a+", encoding="utf-8")
    try:
        fcntl.lockf(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        fh.close()
        if exc.errno in (errno.EACCES, errno.EAGAIN):
            raise Busy from exc
        raise
    meta = {
        "hostname": socket.gethostname(),
        "pid": os.getpid(),
        "acquired_at": datetime.now(timezone.utc).isoformat(),
    }
    fh.seek(0)
    fh.truncate()
    json.dump(meta, fh, indent=2)
    fh.write("\n")
    fh.flush()
    os.fsync(fh.fileno())
    return fh


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(
            "usage: nlm_probe.py <lock-path> hold|try|hold-kill [seconds]",
            file=sys.stderr,
        )
        return 2
    path = Path(argv[1])
    mode = argv[2]
    try:
        if mode == "try":
            fh = acquire(path)
            fcntl.lockf(fh.fileno(), fcntl.LOCK_UN)
            fh.close()
            print("OK", flush=True)
            return 0
        if mode == "hold":
            seconds = float(argv[3]) if len(argv) > 3 else 60.0
            fh = acquire(path)
            print("HELD", flush=True)
            time.sleep(seconds)
            fcntl.lockf(fh.fileno(), fcntl.LOCK_UN)
            fh.close()
            print("RELEASED", flush=True)
            return 0
        if mode == "hold-kill":
            fh = acquire(path)
            print("HELD", flush=True)
            while True:
                time.sleep(3600)
    except Busy:
        print("BUSY", flush=True)
        return 0
    print(f"unknown mode {mode}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
