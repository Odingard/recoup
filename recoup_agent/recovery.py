"""Finding lifecycle transition rules.

Lifecycle: open -> approved -> invoiced -> recovered.
Side states: rejected, disputed, written_off.
"""
from __future__ import annotations

LEGAL_TRANSITIONS: dict[str, set[str]] = {
    "open": {"approved", "rejected"},
    "approved": {"invoiced", "recovered", "written_off", "rejected"},
    "invoiced": {"recovered", "disputed", "written_off"},
    "disputed": {"recovered", "written_off"},
    "rejected": set(),
    "recovered": set(),
    "written_off": set(),
}


def assert_transition(current: str, new: str) -> None:
    """Raise ValueError if moving a finding from `current` to `new` is illegal."""
    allowed = LEGAL_TRANSITIONS.get(current, set())
    if new not in allowed:
        raise ValueError(
            f"cannot move finding from '{current}' to '{new}'; allowed: {sorted(allowed) or 'none'}"
        )
