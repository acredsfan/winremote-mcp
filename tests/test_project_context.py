import json
from pathlib import Path

from winremote import project_context


def test_collect_project_context_detects_scripts(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": "pytest", "build": "echo build"}}),
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('x')", encoding="utf-8")

    payload = project_context.collect_project_context(
        root=str(tmp_path),
        max_files=20,
        include_git_status=False,
        include_package_scripts=True,
        include_recent_errors=False,
    )

    assert payload["package_manager"] == "npm"
    assert payload["package_scripts"]["test"] == "pytest"
    assert payload["file_count"] >= 1
