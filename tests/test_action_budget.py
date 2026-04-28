from winremote import action_budget


def setup_function():
    action_budget.reset(unpause=True)
    action_budget.configure(
        max_clicks_per_minute=20,
        max_keystrokes_per_minute=2000,
        max_shell_commands_per_minute=10,
        max_computer_use_steps=25,
        pause_on_repeated_failure=True,
        repeated_failure_threshold=3,
    )


def test_action_budget_click_limit_pauses_when_exceeded():
    action_budget.configure(max_clicks_per_minute=2)

    ok1, reason1 = action_budget.check_and_record("click", amount=1)
    ok2, reason2 = action_budget.check_and_record("click", amount=1)
    ok3, reason3 = action_budget.check_and_record("click", amount=1)

    assert ok1 is True and reason1 is None
    assert ok2 is True and reason2 is None
    assert ok3 is False
    assert "exceeded" in (reason3 or "")
    assert action_budget.status()["paused"] is True


def test_action_budget_repeated_failures_trigger_pause():
    action_budget.configure(repeated_failure_threshold=2, pause_on_repeated_failure=True)

    action_budget.record_failure("shell")
    assert action_budget.status()["paused"] is False

    action_budget.record_failure("shell")
    status = action_budget.status()
    assert status["paused"] is True
    assert "Repeated failures" in (status["pause_reason"] or "")


def test_action_budget_reset_clears_pause_and_counters():
    action_budget.configure(max_shell_commands_per_minute=1)
    action_budget.check_and_record("shell", amount=1)
    action_budget.check_and_record("shell", amount=1)
    assert action_budget.status()["paused"] is True

    reset = action_budget.reset(unpause=True)
    assert reset["paused"] is False
    assert reset["recent_counts"]["shell"] == 0
