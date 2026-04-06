"""Tests for launcher_history: JSONL event store."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from winremote.launcher_history import EventType, HistoryEvent, HistoryStore, MAX_FILE_SIZE_BYTES


# ---------------------------------------------------------------------------
# HistoryEvent serialisation
# ---------------------------------------------------------------------------

def test_event_roundtrip():
    evt = HistoryEvent(EventType.SERVER_START, timestamp=1000.0, data={"profile": "copilot"})
    line = evt.to_jsonl_line()
    parsed = HistoryEvent.from_dict(json.loads(line))
    assert parsed.event_type == EventType.SERVER_START
    assert parsed.timestamp == 1000.0
    assert parsed.data == {"profile": "copilot"}


def test_event_defaults():
    before = time.time()
    evt = HistoryEvent(EventType.LAUNCHER_START)
    after = time.time()
    assert before <= evt.timestamp <= after
    assert evt.data == {}


# ---------------------------------------------------------------------------
# HistoryStore basic writes and reads
# ---------------------------------------------------------------------------

def test_append_and_tail(tmp_path: Path):
    store = HistoryStore(path=tmp_path / "hist.jsonl")
    for i in range(10):
        store.append(HistoryEvent(EventType.SERVER_START, data={"i": i}))
    events = store.tail(5)
    assert len(events) == 5
    assert events[-1].data["i"] == 9


def test_tail_fewer_than_n(tmp_path: Path):
    store = HistoryStore(path=tmp_path / "hist.jsonl")
    store.append(HistoryEvent(EventType.SERVER_STOP))
    events = store.tail(100)
    assert len(events) == 1


def test_query_by_type(tmp_path: Path):
    store = HistoryStore(path=tmp_path / "hist.jsonl")
    store.append(HistoryEvent(EventType.SERVER_START))
    store.append(HistoryEvent(EventType.TUNNEL_START))
    store.append(HistoryEvent(EventType.SERVER_STOP))
    results = store.query(event_type=EventType.TUNNEL_START)
    assert len(results) == 1
    assert results[0].event_type == EventType.TUNNEL_START


def test_query_since(tmp_path: Path):
    store = HistoryStore(path=tmp_path / "hist.jsonl")
    old_ts = time.time() - 3600
    new_ts = time.time()
    store.append(HistoryEvent(EventType.SERVER_START, timestamp=old_ts))
    store.append(HistoryEvent(EventType.SERVER_START, timestamp=new_ts))
    results = store.query(since=new_ts - 1)
    assert len(results) == 1
    assert results[0].timestamp >= new_ts - 1


def test_all_events(tmp_path: Path):
    store = HistoryStore(path=tmp_path / "hist.jsonl")
    for evt_type in [EventType.SERVER_START, EventType.TUNNEL_START, EventType.LAUNCHER_STOP]:
        store.append(HistoryEvent(evt_type))
    events = store.all_events()
    assert len(events) == 3


def test_empty_store_tail(tmp_path: Path):
    store = HistoryStore(path=tmp_path / "hist.jsonl")
    assert store.tail(10) == []


def test_corrupt_lines_skipped(tmp_path: Path):
    p = tmp_path / "hist.jsonl"
    p.write_text('{"event_type":"server_start","timestamp":1.0,"data":{}}\nNOT_JSON\n', encoding="utf-8")
    store = HistoryStore(path=p)
    events = store.all_events()
    assert len(events) == 1


# ---------------------------------------------------------------------------
# Rotation
# ---------------------------------------------------------------------------

def test_rotation_drops_old_events(tmp_path: Path, monkeypatch):
    """Rotation should drop events older than MAX_RETENTION_DAYS."""
    store = HistoryStore(path=tmp_path / "hist.jsonl")
    ancient_ts = time.time() - (31 * 86400)
    recent_ts = time.time() - 10

    store.append(HistoryEvent(EventType.SERVER_START, timestamp=ancient_ts))
    store.append(HistoryEvent(EventType.SERVER_START, timestamp=recent_ts))

    # Force rotation by patching stat.st_size above threshold
    import os
    original_stat = os.stat

    def fake_stat(path, **kwargs):
        result = original_stat(path, **kwargs)
        # Return a result with a large st_size
        import stat as stat_mod
        class _FakeStat:
            st_size = MAX_FILE_SIZE_BYTES + 1
            # delegate all other attrs
            def __getattr__(self, name):
                return getattr(result, name)
        return _FakeStat()

    monkeypatch.setattr(os, "stat", fake_stat)

    # Trigger rotation through append
    store.append(HistoryEvent(EventType.SERVER_STOP, timestamp=time.time()))

    events = store.all_events()
    old = [e for e in events if e.timestamp <= ancient_ts + 1]
    assert len(old) == 0, "Old events should have been rotated out"


# ---------------------------------------------------------------------------
# Thread safety smoke test
# ---------------------------------------------------------------------------

def test_concurrent_appends(tmp_path: Path):
    import threading
    store = HistoryStore(path=tmp_path / "hist.jsonl")
    errors: list[Exception] = []

    def _writer(n: int) -> None:
        try:
            for _ in range(n):
                store.append(HistoryEvent(EventType.TOOL_CALL, data={"x": 1}))
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=_writer, args=(50,)) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    events = store.all_events()
    assert len(events) == 200
