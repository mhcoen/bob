"""Limits shared by single-task and batch requirement recovery."""

from dataclasses import dataclass


@dataclass(frozen=True)
class RecoveryPolicy:
    editor_repairs: int = 1
    requests_per_part: int = 3
    transport_retries: int = 1
    verdict_retries: int = 1
    review_parts: int = 4
    review_seconds: int = 600
    request_seconds: int = 180

    def can_repair(self, review, repairs: int, attempts_remaining: bool) -> bool:
        return (
            attempts_remaining
            and repairs < self.editor_repairs
            and (not review.blocked or review.failure_kind == "evidence")
        )


RECOVERY = RecoveryPolicy()
