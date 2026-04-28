from winremote.profile_loader import list_profile_tomls, load_profile_toml
from winremote.tiers import normalize_profile_name, resolve_enabled_tools


def test_profile_tomls_are_discoverable():
    names = list_profile_tomls()
    assert "record-only" in names
    assert "safe-observe-only" in names


def test_record_only_profile_toml_loads():
    profile = load_profile_toml("record-only")
    assert profile["name"] == "record-only"
    assert "StartScreenRecording" in profile["enabled_tools"]


def test_normalize_profile_name_accepts_toml_profile():
    assert normalize_profile_name("record-only") == "record-only"


def test_resolve_record_only_profile_tools():
    enabled = resolve_enabled_tools(profile="record-only")
    assert "StartScreenRecording" in enabled
    assert "StopScreenRecording" in enabled
    assert "AnalyzeRecording" in enabled
    assert "RenderSessionReport" in enabled
    assert "Click" not in enabled
    assert "Shell" not in enabled


def test_resolve_safe_observe_only_profile_tools():
    enabled = resolve_enabled_tools(profile="safe-observe-only")
    assert "ObserveScreen" in enabled
    assert "AnalyzeRecording" in enabled
    assert "RenderSessionReport" in enabled
    assert "Click" not in enabled
    assert "ComputerUseStep" not in enabled
