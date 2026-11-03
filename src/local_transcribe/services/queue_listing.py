"""Read-only reporting helpers for ``lt queue list``.

The queue is file-backed rather than a SQL database, so status filtering maps
to state directories and ordering/pagination happen after the matching records
are loaded.  Everything here is pure and read-only.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from typing import Literal

from local_transcribe.services.queue_models import Execution

SortOrder = Literal["oldest", "newest"]
VALID_SORT_ORDERS: tuple[SortOrder, ...] = ("oldest", "newest")


def parse_timestamp(value: str | None) -> datetime | None:
    """Parse an ISO 8601 timestamp, returning ``None`` when it is unusable.

    Queue records normally carry an explicit UTC offset.  A naive value is
    interpreted in the local timezone so older/hand-written records remain
    sortable without rewriting them.
    """
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return parsed


def _offset_label(moment: datetime) -> str:
    raw = moment.strftime("%z")
    if not raw:
        return "UTC"
    return f"UTC{raw[:3]}:{raw[3:]}"


def format_local_timestamp(value: str | None) -> str:
    """Render an ISO timestamp in local time with timezone identification."""
    parsed = parse_timestamp(value)
    if parsed is None:
        return value or "(missing)"
    local = parsed.astimezone()
    zone = local.tzname() or "local"
    return f"{local:%Y-%m-%d %H:%M:%S} {zone} ({_offset_label(local)})"


def local_timezone_label(now: datetime | None = None) -> str:
    """Describe the local timezone used by :func:`format_local_timestamp`."""
    moment = (now or datetime.now(timezone.utc)).astimezone()
    zone = moment.tzname() or "local"
    return f"{zone} ({_offset_label(moment)})"


def format_age(created_at: str | None, *, now: datetime | None = None) -> str:
    """Return a compact age relative to ``now`` (default: current UTC time)."""
    parsed = parse_timestamp(created_at)
    if parsed is None:
        return "unknown"
    current = now or datetime.now(timezone.utc)
    delta = current - parsed.astimezone(timezone.utc)
    total_seconds = int(delta.total_seconds())
    if total_seconds < 0:
        return "future"
    if total_seconds < 60:
        return f"{total_seconds}s"
    minutes, seconds = divmod(total_seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds}s" if seconds else f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h"


def _sort_key(execution: Execution) -> tuple[float, str]:
    parsed = parse_timestamp(execution.created_at)
    epoch = parsed.timestamp() if parsed is not None else float("-inf")
    return epoch, execution.execution_id


def sort_executions(
    executions: Iterable[Execution], *, sort: SortOrder = "oldest"
) -> list[Execution]:
    """Sort by original enqueue time, then execution ID for stable ties."""
    if sort not in VALID_SORT_ORDERS:
        raise ValueError(f"Unknown sort order: {sort!r}")
    if sort == "newest":
        return sorted(executions, key=lambda ex: (-_sort_key(ex)[0], ex.execution_id))
    return sorted(executions, key=_sort_key)


def paginate_executions(
    executions: Sequence[Execution],
    *,
    offset: int = 0,
    limit: int = 60,
) -> tuple[list[Execution], int]:
    """Return one page plus the total matching count.

    Offset is applied after filtering and sorting.  Callers validate the
    arguments; this helper stays intentionally small and read-only.
    """
    total = len(executions)
    return list(executions[offset : offset + limit]), total
