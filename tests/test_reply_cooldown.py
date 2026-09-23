from reply_cooldown import ReplyCooldown, best_available


def test_same_reply_cools_down_for_ten_minutes_in_one_group():
    cooldown = ReplyCooldown(600)
    cooldown.mark_sent("group-a", "  同一句\n回复  ", 100.0)

    assert not cooldown.ready("group-a", "同一句 回复", 699.0)
    assert cooldown.ready("group-a", "同一句 回复", 700.0)
    assert cooldown.ready("group-b", "同一句 回复", 101.0)
    assert cooldown.ready("group-a", "另一句", 101.0)


def test_next_eligible_candidate_can_be_sent_without_resetting_first_cooldown():
    cooldown = ReplyCooldown(600)
    cooldown.mark_sent("group-a", "高分回复", 100.0)
    candidates = [(0.92, "高分回复"), (0.87, "次高分回复")]

    assert best_available(candidates, "group-a", cooldown, 101.0) == "次高分回复"
    cooldown.mark_sent("group-a", "次高分回复", 101.0)
    assert best_available(candidates, "group-a", cooldown, 102.0) is None
    assert best_available(candidates, "group-a", cooldown, 700.0) == "高分回复"


def test_failed_or_probability_skipped_send_does_not_start_cooldown():
    cooldown = ReplyCooldown(600)
    candidates = [(0.9, "尚未成功发送")]

    assert best_available(candidates, "group-a", cooldown, 100.0) == "尚未成功发送"
    assert best_available(candidates, "group-a", cooldown, 101.0) == "尚未成功发送"


def test_zero_disables_content_cooldown():
    cooldown = ReplyCooldown(0)
    cooldown.mark_sent("group-a", "同一句", 100.0)

    assert cooldown.ready("group-a", "同一句", 100.0)
