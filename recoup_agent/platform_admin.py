"""Operator allowlist and platform-admin authorization."""
from __future__ import annotations

import os


def operator_emails() -> set[str]:
    return {
        email.strip().lower()
        for email in os.getenv("RECOUP_OPERATOR_EMAILS", "").split(",")
        if email.strip()
    }


def is_operator(user: dict | None) -> bool:
    if not user or user.get("sample_source"):
        return False
    email = (user.get("email") or "").lower()
    return bool(email) and email in operator_emails()
