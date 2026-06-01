from __future__ import annotations

import streamlit as st

from director_app.state import get_project, project_counts
from director_app.ui_workbench.common import section_title


def render(session_state: dict) -> None:
    project = get_project(session_state)
    counts = project_counts(project)
    section_title("总览", "这版只做导演工作台：拆剧本、整理 references、生成可复制提示词。")

    st.markdown(f"**{project.get('title', '未命名项目')}**")
    cols = st.columns(5)
    for col, key, label in zip(
        cols,
        ["scenes", "beats", "characters", "panel_prompts", "video_prompts"],
        ["Scenes", "Beats", "Characters", "Grid Prompts", "Video Prompts"],
    ):
        col.metric(label, counts[key])

    st.divider()
    if not project.get("raw_script"):
        st.info("先在左侧或「剧本」页粘贴剧本。")
    elif not project.get("beats"):
        st.info("下一步：到「剧本」页点击「自动拆剧本」。")
    elif counts["shot_fields"] < counts["beats"]:
        st.info("下一步：到「分镜」页为 scene 生成镜头字段。")
    elif counts["panel_prompts"] == 0:
        st.info("下一步：到「分镜」页生成宫格提示词。")
    elif counts["video_prompts"] == 0:
        st.info("下一步：到「分镜」页生成视频提示词。")
    else:
        st.success("Prompt Queue 已经有内容，可以到「成片」页编辑和复制。")

    with st.expander("当前结构", expanded=False):
        st.json({
            "director_project": {
                "title": project.get("title"),
                "raw_script": bool(project.get("raw_script")),
                "scenes": counts["scenes"],
                "beats": counts["beats"],
                "references": {
                    "characters": counts["characters"],
                    "environments": counts["environments"],
                    "props": counts["props"],
                },
                "shot_fields": counts["shot_fields"],
                "panel_prompts": counts["panel_prompts"],
                "video_prompts": counts["video_prompts"],
            }
        })
