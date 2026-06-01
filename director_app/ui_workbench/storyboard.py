from __future__ import annotations

import streamlit as st

from director_app.services import generate_grid_prompt, generate_shot_fields, generate_video_prompts
from director_app.state import get_project, grouped_beats, set_project
from director_app.ui_workbench.common import copy_box, section_title


def render(session_state: dict) -> None:
    project = get_project(session_state)
    section_title("分镜", "以 beat 为中心生成镜头字段、宫格提示词和视频提示词。")

    groups = grouped_beats(project)
    if not groups:
        st.info("还没有 beats。请先到「剧本」页自动拆剧本。")
        return

    scene_ids = sorted(groups)
    left, center, right = st.columns([0.9, 1.2, 1.3])

    with left:
        selected_scene = st.selectbox(
            "Scene",
            scene_ids,
            format_func=lambda sid: f"Scene {sid} · {len(groups[sid])} beats",
        )
        beats = groups[selected_scene]
        beat_ids = [b.get("beat_id", "") for b in beats]
        selected_beat_id = st.radio("Shot / Beat", beat_ids, label_visibility="collapsed")

    beat = next((b for b in groups[selected_scene] if b.get("beat_id") == selected_beat_id), groups[selected_scene][0])

    with center:
        st.markdown(f"**{beat.get('beat_id', '')} · {beat.get('type', '')}**")
        st.write(beat.get("raw_text", ""))
        st.caption(f"角色: {', '.join(beat.get('characters') or []) or '无'}")
        st.caption(f"场景: {beat.get('scene_name') or beat.get('scene') or selected_scene}")
        st.divider()
        st.markdown("**镜头字段**")
        st.json(project.get("shot_fields", {}).get(selected_beat_id, {}))

    with right:
        st.markdown("**提示词面板**")
        col_a, col_b, col_c = st.columns(3)
        with col_a:
            if st.button("生成镜头字段", use_container_width=True):
                _run_generation(session_state, lambda: generate_shot_fields(project, selected_scene))
        with col_b:
            if st.button("生成宫格提示词", use_container_width=True):
                _run_generation(session_state, lambda: generate_grid_prompt(project, selected_scene))
        with col_c:
            if st.button("生成视频提示词", use_container_width=True):
                _run_generation(session_state, lambda: generate_video_prompts(project, selected_scene))

        panel = project.get("panel_prompts", {}).get(str(selected_scene), {})
        copy_box("宫格提示词", panel.get("grid_prompt", ""), height=230)

        video_items = [
            item for item in (project.get("video_prompts") or {}).values()
            if isinstance(item, dict) and item.get("from_beat_id") == selected_beat_id
        ]
        video_prompt = video_items[0].get("video_prompt", "") if video_items else ""
        copy_box("当前 beat 视频提示词", video_prompt, height=190)

    if panel:
        with st.expander("Panel 描述", expanded=False):
            for item in panel.get("panel_descs", []):
                st.markdown(f"**Panel {item.get('panel_num')} · {item.get('beat_id')}**")
                st.write(item.get("desc", ""))


def _run_generation(session_state: dict, fn) -> None:
    try:
        with st.spinner("正在调用现有 LLM 链路生成提示词..."):
            next_project = fn()
            set_project(session_state, next_project)
        st.success("已生成。")
        st.rerun()
    except Exception as exc:
        st.error(f"生成失败：{exc}")
