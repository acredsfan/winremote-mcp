import json

import pytest


def test_collect_project_context_tool(monkeypatch: pytest.MonkeyPatch):
    from winremote import __main__ as main

    monkeypatch.setattr(
        main.project_context,
        "collect_project_context",
        lambda **kwargs: {"root": kwargs["root"], "file_count": 2, "files": ["a.py", "b.py"]},
    )

    payload = json.loads(main.CollectProjectContext(root="C:/repo", max_files=50))
    assert payload["root"] == "C:/repo"
    assert payload["file_count"] == 2
