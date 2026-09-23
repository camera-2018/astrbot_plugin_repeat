"""Short-lived, per-group cooldown for a successfully sent repeat reply."""

import hashlib


class ReplyCooldown:
    def __init__(self, seconds: int):
        self.seconds = max(0, seconds)
        self._sent_at: dict[tuple[str, str], float] = {}

    @staticmethod
    def _key(group_id: str, reply: str) -> tuple[str, str]:
        normalized = " ".join(reply.split())
        return group_id, hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def ready(self, group_id: str, reply: str, now: float) -> bool:
        if not self.seconds:
            return True
        sent_at = self._sent_at.get(self._key(group_id, reply))
        return sent_at is None or now - sent_at >= self.seconds

    def mark_sent(self, group_id: str, reply: str, now: float) -> None:
        if not self.seconds:
            return
        self._sent_at = {
            key: sent_at
            for key, sent_at in self._sent_at.items()
            if now - sent_at < self.seconds
        }
        self._sent_at[self._key(group_id, reply)] = now


def best_available(
    candidates: list[tuple[float, str]],
    group_id: str,
    cooldown: ReplyCooldown,
    now: float,
) -> str | None:
    """Return the highest-scoring reply that is not cooling down."""
    for _, reply in sorted(candidates, key=lambda item: item[0], reverse=True):
        if cooldown.ready(group_id, reply, now):
            return reply
    return None
