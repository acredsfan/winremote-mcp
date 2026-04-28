import json


def test_handoff_tools_and_pause_guard(monkeypatch):
    from winremote import __main__ as main

    # ensure clean state
    main.handoff_state.resume_handoff()

    handoff_raw = main.HumanHandoff("Please verify", pause_input_tools=True, show_notification=False)
    handoff = json.loads(handoff_raw)
    assert handoff["success"] is True
    assert handoff["state"]["paused"] is True

    # click should now be paused
    paused_msg = main.Click(10, 20)
    assert "paused" in paused_msg.lower()

    status = json.loads(main.HandoffStatus())
    assert status["paused"] is True

    resumed = json.loads(main.ResumeHumanHandoff())
    assert resumed["success"] is True
    assert resumed["state"]["paused"] is False
