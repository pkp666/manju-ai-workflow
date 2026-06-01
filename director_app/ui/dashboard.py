from __future__ import annotations

import streamlit as st

from director_app.project import completion_ratio, next_actions, project_counts
from director_app.ui.common import section_title


def render(state: dict) -> None:
    section_title("项目总览", "从剧本、资产、分镜到成片的生产状态。")

    counts = project_counts(state)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("镜头", counts["beats"])
    c2.metric("角色资产", f"{counts['char_assets']}/{max(counts['characters'], counts['char_assets'])}")
    c3.metric("关键帧", counts["frames"])
    c4.metric("视频片段", counts["videos"])

    st.progress(
        completion_ratio(counts["shot_fields"], counts["beats"]),
        text=f"镜头字段 {counts['shot_fields']}/{counts['beats']}",
    )
    st.progress(
        completion_ratio(counts["frames"], counts["beats"]),
        text=f"关键帧 {counts['frames']}/{counts['beats']}",
    )

    st.divider()
    left, right = st.columns([1, 2])
    with left:
        st.markdown("**下一步**")
        for item in next_actions(state):
            st.write(f"- {item}")
    with right:
        st.markdown("**项目对象**")
        st.write(
            {
                "scenes": counts["scenes"],
                "characters": counts["characters"],
                "scene_assets": counts["scene_assets"],
                "prop_assets": counts["prop_assets"],
            }
        )
