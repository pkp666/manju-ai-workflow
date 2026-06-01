"""Shared UI helpers for the director workbench."""

from __future__ import annotations

import streamlit as st


def section_title(title: str, caption: str = "") -> None:
    st.subheader(title)
    if caption:
        st.caption(caption)


def status_chip(label: str, active: bool) -> str:
    return f"{'✅' if active else '⬜'} {label}"
