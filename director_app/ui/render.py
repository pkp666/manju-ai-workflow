from __future__ import annotations

import streamlit as st

from director_app.ui.common import section_title


def render(state: dict) -> None:
    section_title("渲染 / 成片", "把帧图、运动提示词和视频结果整理成 Render Queue。")

    video_state = state.get("video_state", {})
    if not video_state:
        st.info("还没有视频任务。旧 Tab7 会迁移到这里。")
        return

    rows = []
    for key, item in video_state.items():
        if not isinstance(item, dict):
            continue
        rows.append({
            "job": key,
            "scene": item.get("seg_id", ""),
            "from": item.get("from_beat_id", ""),
            "to": item.get("to_beat_id", ""),
            "status": item.get("status", "pending"),
            "duration": item.get("duration_hint", ""),
            "video": item.get("video_path", ""),
        })

    st.dataframe(rows, hide_index=True, use_container_width=True)

    selected = st.selectbox("查看任务", [r["job"] for r in rows])
    item = video_state.get(selected, {})
    if item:
        st.text_area("Motion Prompt", value=item.get("video_prompt", ""), height=180, disabled=True)
        if item.get("video_path"):
            st.video(item["video_path"])
