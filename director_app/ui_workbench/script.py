from __future__ import annotations

import streamlit as st

from director_app.services import auto_split_script
from director_app.state import DEFAULT_STYLE_SUFFIX, get_project, grouped_beats, set_project
from director_app.ui_workbench.common import section_title


def render(session_state: dict) -> None:
    project = get_project(session_state)
    section_title("剧本", "粘贴原始剧本后，按最短链路自动拆成 scenes / beats / references。")

    title = st.text_input("项目标题", value=project.get("title", ""))
    raw_script = st.text_area("原始剧本", value=project.get("raw_script", ""), height=260)
    left, right = st.columns(2)
    with left:
        story_tone = st.text_area("故事论调（可选）", value=project.get("story_tone", ""), height=120)
    with right:
        style_suffix = st.text_area(
            "风格后缀（可选）",
            value=project.get("style_suffix") or DEFAULT_STYLE_SUFFIX,
            height=120,
        )

    project.update({
        "title": title,
        "raw_script": raw_script,
        "story_tone": story_tone,
        "style_suffix": style_suffix or DEFAULT_STYLE_SUFFIX,
    })
    set_project(session_state, project)

    disabled = not raw_script.strip()
    if st.button("自动拆剧本", type="primary", disabled=disabled):
        try:
            with st.spinner("正在分析角色、拆场景、拆 beats..."):
                next_project = auto_split_script(project)
                set_project(session_state, next_project)
            st.success("拆剧本完成。")
            st.rerun()
        except Exception as exc:
            st.error(f"自动拆剧本失败：{exc}")

    if disabled:
        st.warning("空剧本不能自动拆分。")

    st.divider()
    groups = grouped_beats(project)
    if not groups:
        st.info("还没有 scenes / beats。")
        return

    for sid, beats in groups.items():
        scene_name = beats[0].get("scene_name") or beats[0].get("scene") or f"Scene {sid}"
        with st.expander(f"Scene {sid} · {scene_name} · {len(beats)} beats", expanded=sid == min(groups)):
            for beat in beats:
                st.markdown(f"**{beat.get('beat_id', '')}** · {beat.get('type', '')}")
                st.caption(beat.get("raw_text", "")[:260])
