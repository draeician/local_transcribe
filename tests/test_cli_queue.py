"""CLI queue smoke tests (task 022) and queue-list reporting tests (task 037)."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from local_transcribe.cli import app
from local_transcribe.services.queue_models import (
    Execution,
    ExecutionError,
    utc_now_iso,
)
from local_transcribe.services.queue_paths import initialize_queue_layout
from local_transcribe.services.queue_store import QueueStore
from local_transcribe.services.worker import claim_execution, fail_or_retry

runner = CliRunner()

STATUSES = ("pending", "processing", "retry", "completed", "failed", "cancelled")


@pytest.fixture(autouse=True)
def _isolated_xdg_config(tmp_path: Path, monkeypatch) -> None:
    """Keep every test off the operator's real config.

    Queue identity validation still runs, but against an empty isolated XDG
    configuration instead of a production config whose ``expected_uuid`` would
    reject the temporary queue used by each test.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))


def _seed_execution(
    queue_dir: Path,
    *,
    execution_id: str,
    status: str,
    created_at: str,
    source: str | None = None,
    priority: int = 50,
    origin: str = "test",
) -> Execution:
    execution = Execution(
        execution_id=execution_id,
        source_key=f"youtube:{execution_id[-11:]}",
        generation=1,
        source=source or f"https://youtu.be/{execution_id[-11:]}",
        source_type="youtube",
        origin=origin,
        priority=priority,
        status=status,  # type: ignore[arg-type]
        created_at=created_at,
        updated_at=created_at,
        available_at=created_at,
    )
    path = queue_dir / status / f"{execution_id}.json"
    path.write_text(json.dumps(execution.to_dict()), encoding="utf-8")
    return execution


def _list(queue_dir: Path, *args: str):
    return runner.invoke(
        app,
        ["queue", "list", *args, "--queue-dir", str(queue_dir)],
    )


def _row_ids(output: str) -> list[str]:
    ids: list[str] = []
    for line in output.splitlines():
        match = re.match(r"([0-9a-f-]{36})\t", line)
        if match:
            ids.append(match.group(1))
    return ids


def _seed_one_per_status(queue_dir: Path) -> dict[str, str]:
    ids: dict[str, str] = {}
    for index, status in enumerate(STATUSES):
        execution_id = f"{index + 1:08d}-1111-4111-8111-{index + 1:012d}"
        ids[status] = execution_id
        _seed_execution(
            queue_dir,
            execution_id=execution_id,
            status=status,
            created_at=f"2026-11-01T00:00:0{index}+00:00",
        )
    return ids


def test_queue_init_and_add(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
    q = tmp_path / "queue"
    r = runner.invoke(app, ["queue", "init", "--queue-dir", str(q)])
    assert r.exit_code == 0, r.output
    assert "Wrote config:" in r.output
    cfg = tmp_path / "xdg-config" / "local-transcribe" / "config.yaml"
    assert cfg.is_file()
    assert "expected_uuid:" in cfg.read_text(encoding="utf-8")
    r2 = runner.invoke(
        app,
        ["queue", "add", "https://youtu.be/dQw4w9WgXcQ", "--queue-dir", str(q)],
    )
    assert r2.exit_code == 0, r2.output
    assert "enqueued" in r2.output
    r3 = runner.invoke(app, ["queue", "stats", "--queue-dir", str(q)])
    assert r3.exit_code == 0
    assert "pending:" in r3.output


def test_purge_requires_filter(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    q = tmp_path / "queue"
    runner.invoke(app, ["queue", "init", "--queue-dir", str(q)])
    r = runner.invoke(app, ["queue", "purge", "--queue-dir", str(q)])
    assert r.exit_code != 0


def test_queue_retry_help_documents_all_failed() -> None:
    r = runner.invoke(app, ["queue", "retry", "--help"])
    assert r.exit_code == 0
    help_text = r.output.lower()
    assert "all failed" in help_text
    assert "--include-cancelled" in r.output
    assert "lt queue retry" in help_text


def test_queue_retry_all_failed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
    q = tmp_path / "queue"
    initialize_queue_layout(q)
    store = QueueStore(q)
    urls = [
        "https://youtu.be/aaaaaaaaaaa",
        "https://youtu.be/bbbbbbbbbbb",
    ]
    for url in urls:
        result = store.enqueue(url, origin="test")
        assert result.execution is not None
        claimed = claim_execution(q, result.execution)
        assert claimed is not None
        claimed.max_attempts = 1
        claimed.attempts = 1
        fail_or_retry(
            q,
            claimed,
            ExecutionError(
                category="download_failed",
                message="boom",
                retryable=False,
                occurred_at=utc_now_iso(),
            ),
        )

    assert len(store.list_executions(status="failed")) == 2
    r = runner.invoke(app, ["queue", "retry", "--queue-dir", str(q)])
    assert r.exit_code == 0, r.output
    assert "Retry summary:" in r.output
    assert "sources=2" in r.output
    assert len(store.list_executions(status="pending")) == 2


def test_queue_list_shows_full_execution_id(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    q = tmp_path / "queue"
    initialize_queue_layout(q)
    store = QueueStore(q)
    result = store.enqueue("https://youtu.be/ccccccccccc", origin="test")
    assert result.execution is not None
    eid = result.execution.execution_id
    r = runner.invoke(app, ["queue", "list", "--queue-dir", str(q)])
    assert r.exit_code == 0, r.output
    assert eid in r.output
    assert eid[:8] + "…" not in r.output


def test_queue_list_defaults_show_all_records_and_range(tmp_path: Path) -> None:
    q = tmp_path / "queue"
    initialize_queue_layout(q)
    ids = [
        "11111111-1111-4111-8111-111111111111",
        "22222222-2222-4222-8222-222222222222",
        "33333333-3333-4333-8333-333333333333",
    ]
    _seed_execution(
        q, execution_id=ids[0], status="pending", created_at="2026-11-01T10:00:00+00:00"
    )
    _seed_execution(
        q,
        execution_id=ids[1],
        status="completed",
        created_at="2026-11-01T11:00:00+00:00",
    )
    _seed_execution(
        q, execution_id=ids[2], status="failed", created_at="2026-11-01T12:00:00+00:00"
    )

    r = _list(q)

    assert r.exit_code == 0, r.output
    assert _row_ids(r.output) == ids
    assert (
        "EXECUTION_ID\tENQUEUED\tAGE\tSTATUS\tPRIORITY\tSOURCE\tORIGIN"
        in r.output
    )
    assert (
        "Showing 1-3 of 3 matching executions "
        "(limit=60, offset=0, sort=oldest)" in r.output
    )
    assert "Timestamps are local time:" in r.output


@pytest.mark.parametrize("status", STATUSES)
def test_queue_list_filters_each_status(tmp_path: Path, status: str) -> None:
    q = tmp_path / "queue"
    initialize_queue_layout(q)
    ids = _seed_one_per_status(q)

    r = _list(q, status)

    assert r.exit_code == 0, r.output
    assert _row_ids(r.output) == [ids[status]]
    assert "Showing 1-1 of 1 matching executions" in r.output
    for other_status, other_id in ids.items():
        if other_status != status:
            assert other_id not in r.output


def test_queue_list_status_option_remains_compatible(tmp_path: Path) -> None:
    q = tmp_path / "queue"
    initialize_queue_layout(q)
    ids = _seed_one_per_status(q)

    r = _list(q, "--status", "failed")

    assert r.exit_code == 0, r.output
    assert _row_ids(r.output) == [ids["failed"]]


def test_queue_list_uuid_selects_one_execution(tmp_path: Path) -> None:
    q = tmp_path / "queue"
    initialize_queue_layout(q)
    ids = _seed_one_per_status(q)

    r = _list(q, ids["retry"])

    assert r.exit_code == 0, r.output
    assert _row_ids(r.output) == [ids["retry"]]
    assert "Showing 1-1 of 1 matching executions" in r.output


def test_queue_list_unknown_uuid_is_empty_not_an_error(tmp_path: Path) -> None:
    q = tmp_path / "queue"
    initialize_queue_layout(q)
    _seed_one_per_status(q)

    missing = "99999999-9999-4999-8999-999999999999"
    r = _list(q, missing)

    assert r.exit_code == 0, r.output
    assert _row_ids(r.output) == []
    assert f"No matching execution for UUID {missing}." in r.output
    assert "Showing 0-0 of 0 matching executions" in r.output


def test_queue_list_uuid_rejects_nonzero_offset(tmp_path: Path) -> None:
    q = tmp_path / "queue"
    initialize_queue_layout(q)
    ids = _seed_one_per_status(q)
    selected = ids["pending"]

    r = _list(q, selected, "--offset", "1")
    assert r.exit_code != 0
    assert (
        "A UUID selection cannot be combined with a nonzero --offset"
        in r.output
    )
    assert selected not in r.output

    # Zero is compatible and still shows the selected record.
    zero = _list(q, selected, "--offset", "0")
    assert zero.exit_code == 0, zero.output
    assert _row_ids(zero.output) == [selected]

    # Validation happens before lookup: a missing UUID plus nonzero offset is
    # also rejected rather than silently returning an empty list.
    missing = "99999999-9999-4999-8999-999999999999"
    missing_offset = _list(q, missing, "--offset", "1")
    assert missing_offset.exit_code != 0
    assert (
        "A UUID selection cannot be combined with a nonzero --offset"
        in missing_offset.output
    )


def test_queue_list_uuid_applies_origin_filter(tmp_path: Path) -> None:
    q = tmp_path / "queue"
    initialize_queue_layout(q)
    alpha = "11111111-1111-4111-8111-111111111111"
    beta = "22222222-2222-4222-8222-222222222222"
    _seed_execution(
        q,
        execution_id=alpha,
        status="pending",
        created_at="2026-11-01T10:00:00+00:00",
        origin="alpha",
    )
    _seed_execution(
        q,
        execution_id=beta,
        status="pending",
        created_at="2026-11-01T11:00:00+00:00",
        origin="beta",
    )

    match = _list(q, alpha, "--origin", "alpha")
    assert match.exit_code == 0, match.output
    assert _row_ids(match.output) == [alpha]

    mismatch = _list(q, alpha, "--origin", "beta")
    assert mismatch.exit_code == 0, mismatch.output
    assert _row_ids(mismatch.output) == []
    assert (
        f"No matching execution for UUID {alpha} with origin 'beta'."
        in mismatch.output
    )
    assert "Showing 0-0 of 0 matching executions" in mismatch.output


def test_queue_list_empty_queue_and_empty_status_filter(tmp_path: Path) -> None:
    q = tmp_path / "queue"
    initialize_queue_layout(q)

    empty = _list(q)
    assert empty.exit_code == 0, empty.output
    assert _row_ids(empty.output) == []
    assert "No matching executions." in empty.output
    assert "Showing 0-0 of 0 matching executions" in empty.output

    _seed_execution(
        q,
        execution_id="11111111-1111-4111-8111-111111111111",
        status="pending",
        created_at="2026-11-01T10:00:00+00:00",
    )
    filtered = _list(q, "completed")
    assert filtered.exit_code == 0, filtered.output
    assert _row_ids(filtered.output) == []
    assert "No matching executions." in filtered.output
    assert "Showing 0-0 of 0 matching executions" in filtered.output


def test_queue_list_sorts_oldest_and_newest(tmp_path: Path) -> None:
    q = tmp_path / "queue"
    initialize_queue_layout(q)
    old = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    middle = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    new = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
    # Insert out of order so filesystem iteration cannot accidentally pass.
    _seed_execution(
        q, execution_id=middle, status="pending", created_at="2026-11-01T11:00:00+00:00"
    )
    _seed_execution(
        q, execution_id=new, status="pending", created_at="2026-11-01T12:00:00+00:00"
    )
    _seed_execution(
        q, execution_id=old, status="pending", created_at="2026-11-01T10:00:00+00:00"
    )

    oldest = _list(q, "--sort", "oldest")
    newest = _list(q, "--sort", "newest")

    assert oldest.exit_code == 0, oldest.output
    assert newest.exit_code == 0, newest.output
    assert _row_ids(oldest.output) == [old, middle, new]
    assert _row_ids(newest.output) == [new, middle, old]
    assert "(limit=60, offset=0, sort=newest)" in newest.output


def test_queue_list_ties_use_execution_id_as_secondary_key(tmp_path: Path) -> None:
    q = tmp_path / "queue"
    initialize_queue_layout(q)
    low = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    high = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    same_time = "2026-11-01T10:00:00+00:00"
    _seed_execution(q, execution_id=high, status="pending", created_at=same_time)
    _seed_execution(q, execution_id=low, status="pending", created_at=same_time)

    oldest = _list(q, "--sort", "oldest")
    newest = _list(q, "--sort", "newest")

    assert oldest.exit_code == 0, oldest.output
    assert newest.exit_code == 0, newest.output
    assert _row_ids(oldest.output) == [low, high]
    assert _row_ids(newest.output) == [low, high]


def test_queue_list_offset_boundaries(tmp_path: Path) -> None:
    q = tmp_path / "queue"
    initialize_queue_layout(q)
    ids = [
        "11111111-1111-4111-8111-111111111111",
        "22222222-2222-4222-8222-222222222222",
        "33333333-3333-4333-8333-333333333333",
    ]
    for index, execution_id in enumerate(ids):
        _seed_execution(
            q,
            execution_id=execution_id,
            status="pending",
            created_at=f"2026-11-01T1{index}:00:00+00:00",
        )

    middle = _list(q, "--offset", "1", "--limit", "1")
    at_end = _list(q, "--offset", "3")
    beyond = _list(q, "--offset", "99")

    assert middle.exit_code == 0, middle.output
    assert _row_ids(middle.output) == [ids[1]]
    assert "Showing 2-2 of 3 matching executions" in middle.output

    assert at_end.exit_code == 0, at_end.output
    assert _row_ids(at_end.output) == []
    assert "Offset 3 is beyond the matching count (3); no rows shown." in at_end.output
    assert "Showing 0-0 of 3 matching executions" in at_end.output

    assert beyond.exit_code == 0, beyond.output
    assert _row_ids(beyond.output) == []
    assert "Offset 99 is beyond the matching count (3); no rows shown." in beyond.output


def test_queue_list_offset_applies_after_status_filter(tmp_path: Path) -> None:
    q = tmp_path / "queue"
    initialize_queue_layout(q)
    first = "11111111-1111-4111-8111-111111111111"
    second = "22222222-2222-4222-8222-222222222222"
    _seed_execution(
        q, execution_id=first, status="pending", created_at="2026-11-01T10:00:00+00:00"
    )
    _seed_execution(
        q, execution_id=second, status="pending", created_at="2026-11-01T11:00:00+00:00"
    )
    _seed_execution(
        q,
        execution_id="33333333-3333-4333-8333-333333333333",
        status="failed",
        created_at="2026-11-01T09:00:00+00:00",
    )

    r = _list(q, "pending", "--offset", "1", "--limit", "1")

    assert r.exit_code == 0, r.output
    assert _row_ids(r.output) == [second]
    assert "Showing 2-2 of 2 matching executions" in r.output


def test_queue_list_pagination_is_stable_for_unchanged_data(tmp_path: Path) -> None:
    q = tmp_path / "queue"
    initialize_queue_layout(q)
    ids = [f"{index:08d}-1111-4111-8111-{index:012d}" for index in range(1, 6)]
    for index, execution_id in enumerate(ids):
        _seed_execution(
            q,
            execution_id=execution_id,
            status="pending",
            created_at=f"2026-11-01T1{index}:00:00+00:00",
        )

    seen: list[str] = []
    for offset in (0, 2, 4):
        r = _list(q, "--offset", str(offset), "--limit", "2")
        assert r.exit_code == 0, r.output
        assert "of 5 matching executions" in r.output
        seen.extend(_row_ids(r.output))

    assert seen == ids
    assert len(set(seen)) == len(ids)


@pytest.mark.parametrize(
    ("args", "needle"),
    [
        (("--offset", "-1"), "--offset must be zero or greater"),
        (("--limit", "0"), "--limit must be a positive integer"),
        (("--limit", "-5"), "--limit must be a positive integer"),
        (("--sort", "sideways"), "Invalid --sort value"),
        (("bogus",), "Unknown status or invalid UUID"),
        (
            ("12345678-1234-1234-1234-12345678901z",),
            "Invalid UUID",
        ),
        (("--status", "bogus"), "Unknown status"),
        (("pending", "--status", "failed"), "Conflicting status filters"),
    ],
)
def test_queue_list_rejects_invalid_arguments(
    tmp_path: Path, args: tuple[str, ...], needle: str
) -> None:
    q = tmp_path / "queue"
    initialize_queue_layout(q)
    _seed_one_per_status(q)

    r = _list(q, *args)

    assert r.exit_code != 0
    assert needle in r.output


def test_queue_list_rejects_uuid_combined_with_status(tmp_path: Path) -> None:
    q = tmp_path / "queue"
    initialize_queue_layout(q)
    ids = _seed_one_per_status(q)

    r = _list(q, ids["pending"], "--status", "failed")

    assert r.exit_code != 0
    assert "cannot be combined with --status" in r.output


def test_queue_list_filters_by_origin(tmp_path: Path) -> None:
    q = tmp_path / "queue"
    initialize_queue_layout(q)
    alpha = "11111111-1111-4111-8111-111111111111"
    beta = "22222222-2222-4222-8222-222222222222"
    _seed_execution(
        q,
        execution_id=alpha,
        status="pending",
        created_at="2026-11-01T10:00:00+00:00",
        origin="alpha",
    )
    _seed_execution(
        q,
        execution_id=beta,
        status="pending",
        created_at="2026-11-01T11:00:00+00:00",
        origin="beta",
    )

    r = _list(q, "--origin", "alpha")

    assert r.exit_code == 0, r.output
    assert _row_ids(r.output) == [alpha]


def test_queue_list_renders_local_timestamp_and_age(tmp_path: Path) -> None:
    q = tmp_path / "queue"
    initialize_queue_layout(q)
    created_at = "2020-01-02T03:04:05+00:00"
    _seed_execution(
        q,
        execution_id="11111111-1111-4111-8111-111111111111",
        status="pending",
        created_at=created_at,
    )

    r = _list(q)

    assert r.exit_code == 0, r.output
    expected_local = datetime.fromisoformat(created_at).astimezone()
    assert expected_local.strftime("%Y-%m-%d %H:%M:%S") in r.output
    assert re.search(r"\(UTC[+-]\d{2}:\d{2}\)", r.output)
    assert re.search(r"\t\d+d \d+h\t", r.output)


def test_queue_list_is_read_only(tmp_path: Path) -> None:
    q = tmp_path / "queue"
    initialize_queue_layout(q)
    _seed_one_per_status(q)

    def snapshot() -> dict[str, bytes]:
        return {
            str(path.relative_to(q)): path.read_bytes()
            for path in sorted(q.rglob("*"))
            if path.is_file()
        }

    before = snapshot()
    r = _list(q)
    after = snapshot()

    assert r.exit_code == 0, r.output
    assert after == before
