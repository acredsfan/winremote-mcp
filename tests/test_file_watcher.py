from pathlib import Path

from winremote import file_watcher


def test_file_watcher_start_list_stop(tmp_path: Path):
    root = tmp_path / "watch_root"
    root.mkdir()

    initial = root / "a.txt"
    initial.write_text("hello", encoding="utf-8")

    started = file_watcher.start_file_watch(str(root), recursive=True)
    watch_id = started["watch_id"]
    assert started["baseline_file_count"] == 1

    # create
    created = root / "b.txt"
    created.write_text("new", encoding="utf-8")

    # modify
    initial.write_text("hello world", encoding="utf-8")

    changes = file_watcher.list_file_changes(watch_id=watch_id)
    types = {item["type"] for item in changes}
    assert "created" in types
    assert "modified" in types

    # delete
    created.unlink()
    changes2 = file_watcher.list_file_changes(watch_id=watch_id)
    assert any(item["type"] == "deleted" for item in changes2)

    stopped = file_watcher.stop_file_watch(watch_id)
    assert stopped["stopped"] is True
    assert stopped["change_count"] >= 2
