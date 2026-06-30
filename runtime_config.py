from __future__ import annotations

import os
from typing import Any


def config_value(name: str, default: str = "") -> str:
    value = os.getenv(name)
    if value not in (None, ""):
        return str(value)
    try:
        import streamlit as st

        secret_value: Any = st.secrets.get(name)
        if secret_value not in (None, ""):
            return str(secret_value)
    except Exception:
        pass
    return default


def config_bool(name: str, default: bool = False) -> bool:
    raw = config_value(name, "")
    if raw == "":
        return default
    return raw.casefold() in {"1", "true", "yes", "y", "on"}


def config_int(name: str, default: int) -> int:
    raw = config_value(name, "")
    if raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def config_csv(name: str, default: str = "") -> list[str]:
    raw = config_value(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]
