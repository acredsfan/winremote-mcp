from winremote import handoff_state


def test_handoff_pause_resume_status():
    handoff_state.resume_handoff()

    state = handoff_state.request_handoff(message="Please handle MFA", pause_input_tools=True)
    assert state["paused"] is True
    assert handoff_state.is_paused() is True

    resumed = handoff_state.resume_handoff()
    assert resumed["paused"] is False
    assert handoff_state.is_paused() is False


def test_handoff_timeout_auto_resume():
    handoff_state.resume_handoff()
    handoff_state.request_handoff(message="short", pause_input_tools=True, timeout_seconds=0.01)

    # Trigger refresh by checking status repeatedly
    import time

    time.sleep(0.03)
    state = handoff_state.status()
    assert state["paused"] is False
