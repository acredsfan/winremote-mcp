import json
from pathlib import Path


def test_file_watch_tools_roundtrip(tmp_path: Path):
    from winremote import __main__ as main

    root = tmp_path / "tool_watch"
    root.mkdir()

    start_raw = main.StartFileWatch(str(root))
    started = json.loads(start_raw)
    watch_id = started["watch_id"]

    (root / "x.txt").write_text("x", encoding="utf-8")

    list_raw = main.ListFileChanges(watch_id=watch_id)
    listed = json.loads(list_raw)
    assert any(item["type"] == "created" for item in listed)

    stop_raw = main.StopFileWatch(watch_id)
    stopped = json.loads(stop_raw)
    assert stopped["stopped"] is True
