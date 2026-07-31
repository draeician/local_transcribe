"""Tests for generation-aware transcript publisher (task 015 / 034)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from local_transcribe.services.transcript_publish import (
    TranscriptPublishError,
    publish_transcript,
)
from local_transcribe.services.transcriber import TranscribeConfig, transcribe_local_file


def _payload(text: str = "hello world", vid: str = "dQw4w9WgXcQ") -> dict:
    return {
        "transcript": text,
        "duration": 10,
        "comments": [],
        "metadata": {"id": vid, "title": "t", "channel": "c", "published_at": None},
    }


def test_publish_success(tmp_path: Path) -> None:
    result = publish_transcript(
        tmp_path,
        source_key="youtube:dQw4w9WgXcQ",
        payload=_payload(),
        generation=1,
    )
    assert result.path.is_file()
    assert json.loads(result.path.read_text())["transcript"] == "hello world"
    assert (tmp_path / ".generations" / "dQw4w9WgXcQ" / "000001.json").is_file()
    assert (tmp_path / ".dQw4w9WgXcQ.json.generation").is_file()


def test_stale_generation_refused(tmp_path: Path) -> None:
    publish_transcript(
        tmp_path,
        source_key="youtube:dQw4w9WgXcQ",
        payload=_payload("gen2"),
        generation=2,
    )
    with pytest.raises(TranscriptPublishError, match="Refusing"):
        publish_transcript(
            tmp_path,
            source_key="youtube:dQw4w9WgXcQ",
            payload=_payload("gen1-stale"),
            generation=1,
        )
    assert json.loads((tmp_path / "dQw4w9WgXcQ.json").read_text())["transcript"] == "gen2"


def test_empty_transcript_rejected(tmp_path: Path) -> None:
    with pytest.raises(TranscriptPublishError, match="empty"):
        publish_transcript(
            tmp_path,
            source_key="youtube:dQw4w9WgXcQ",
            payload=_payload(""),
            generation=1,
        )
    assert not (tmp_path / "dQw4w9WgXcQ.json").exists()


def test_no_plain_open_w_on_final_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Publisher must not truncate the final path via open(..., 'w')."""
    import builtins

    final = tmp_path / "dQw4w9WgXcQ.json"
    real_open = builtins.open

    def guarded_open(file, mode="r", *args, **kwargs):  # type: ignore[no-untyped-def]
        path = Path(file)
        if path.resolve() == final.resolve() and isinstance(mode, str) and "w" in mode:
            raise AssertionError(f"plain open({mode!r}) on final transcript path")
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_open)
    publish_transcript(
        tmp_path,
        source_key="youtube:dQw4w9WgXcQ",
        payload=_payload("atomic"),
        generation=1,
    )
    assert final.is_file()


def test_direct_local_uses_publisher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audio = tmp_path / "talk.m4a"
    audio.write_bytes(b"data")
    out = tmp_path / "out"
    out.mkdir()
    published: list[str] = []

    def fake_publish(root, *, source_key, payload, generation, allow_empty=False):  # type: ignore[no-untyped-def]
        published.append(source_key)
        from local_transcribe.services.transcript_publish import PublishResult

        path = Path(root) / "talk.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return PublishResult(path=path, generation=generation)

    monkeypatch.setattr(
        "local_transcribe.services.transcriber.transcribe_audio",
        lambda *a, **k: "direct text",
    )
    monkeypatch.setattr(
        "local_transcribe.services.transcriber.publish_transcript", fake_publish
    )

    cfg = TranscribeConfig(output_dir=out, model="tiny", device="cpu")
    path = transcribe_local_file(audio, cfg)
    assert path.is_file()
    assert published == ["file:talk"]
    assert "direct text" in path.read_text(encoding="utf-8")


def test_file_source_key_stem(tmp_path: Path) -> None:
    result = publish_transcript(
        tmp_path,
        source_key="file:my_recording",
        payload=_payload("local", vid="my_recording"),
        generation=1,
    )
    assert result.path.name == "my_recording.json"
