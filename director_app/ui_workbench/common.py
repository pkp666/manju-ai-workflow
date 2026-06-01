from __future__ import annotations

import streamlit as st


def section_title(title: str, caption: str = "") -> None:
    st.subheader(title)
    if caption:
        st.caption(caption)


def copy_box(label: str, value: str, height: int = 220) -> None:
    st.text_area(label, value=value or "", height=height)


def status_text(done: bool) -> str:
    return "ready" if done else "draft"
