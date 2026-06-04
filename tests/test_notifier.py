from watchtower.notifier import NotificationDecider


def _event(kind, session="s1", ts="2026-06-04T10:00:00+00:00", **extra):
    return {"event": kind, "session_id": session, "ts": ts,
            "cwd": "C:\\Users\\aigor\\_dev\\_paytable\\payTableDotnet", **extra}


def test_notification_event_always_toasts_with_message():
    decider = NotificationDecider()
    result = decider.decide(_event("Notification", message="Claude needs permission to run Bash"))
    assert result is not None
    assert result["title"] == "Waiting for you - payTableDotnet"
    assert "permission" in result["message"]


def test_short_turn_stop_is_silent():
    decider = NotificationDecider(min_turn_seconds=45)
    decider.decide(_event("UserPromptSubmit", ts="2026-06-04T10:00:00+00:00"))
    result = decider.decide(_event("Stop", ts="2026-06-04T10:00:20+00:00"))
    assert result is None  # 20s conversational turn - no spam


def test_long_turn_stop_toasts_with_duration():
    decider = NotificationDecider(min_turn_seconds=45)
    decider.decide(_event("UserPromptSubmit", ts="2026-06-04T10:00:00+00:00"))
    result = decider.decide(_event("Stop", ts="2026-06-04T10:03:30+00:00"))
    assert result is not None
    assert result["title"] == "Session finished - payTableDotnet"
    assert "3m 30s" in result["message"]


def test_stop_without_known_prompt_is_silent():
    decider = NotificationDecider()
    assert decider.decide(_event("Stop")) is None


def test_cooldown_suppresses_rapid_repeats():
    decider = NotificationDecider(cooldown_seconds=10)
    first = decider.decide(_event("Notification", ts="2026-06-04T10:00:00+00:00", message="a"))
    second = decider.decide(_event("Notification", ts="2026-06-04T10:00:05+00:00", message="b"))
    third = decider.decide(_event("Notification", ts="2026-06-04T10:00:15+00:00", message="c"))
    assert first is not None
    assert second is None  # within cooldown
    assert third is not None


def test_sessions_are_independent():
    decider = NotificationDecider(min_turn_seconds=45)
    decider.decide(_event("UserPromptSubmit", session="a", ts="2026-06-04T10:00:00+00:00"))
    decider.decide(_event("UserPromptSubmit", session="b", ts="2026-06-04T10:02:00+00:00"))
    result_a = decider.decide(_event("Stop", session="a", ts="2026-06-04T10:02:30+00:00"))
    result_b = decider.decide(_event("Stop", session="b", ts="2026-06-04T10:02:30+00:00"))
    assert result_a is not None  # 2m30s turn
    assert result_b is None      # 30s turn
