from __future__ import annotations

import streamlit as st

from director_app.project import grouped_beats
from director_app.ui.common import section_title


def render(state: dict) -> None:
    section_title("剧本 / 分场", "把原文、论调、场景和 beats 放在同一个创作计划里。")

    rs = state.get("rs", {})
    raw = state.get("raw_script") or rs.get("raw", "")
    tone = state.get("story_tone") or rs.get("tone_text", "")

    left, right = st.columns([1, 1])
    with left:
        st.markdown("**故事论调**")
        st.text_area("tone", value=tone, height=140, label_visibility="collapsed", disabled=True)
    with right:
        st.markdown("**原文 / 改编文本**")
        st.text_area("raw", value=raw[:4000], height=140, label_visibility="collapsed", disabled=True)

    st.divider()
    st.markdown("**场景与镜头计划**")
    groups = grouped_beats(state)
    if not groups:
        st.info("还没有 beats。旧系统里的 Tab0 + Tab1 会迁移到这里。")
        return

    for sid, beats in groups.items():
        scene_name = beats[0].get("scene_name") or beats[0].get("scene") or f"Scene {sid}"
        with st.expander(f"Scene {sid} · {scene_name} · {len(beats)} shots", expanded=sid == min(groups)):
            for beat in beats:
                st.markdown(f"**{beat.get('beat_id', '')}** · {beat.get('type', '')} · {beat.get('speaker', '') or '无说话人'}")
                st.caption(beat.get("raw_text", "")[:220])
