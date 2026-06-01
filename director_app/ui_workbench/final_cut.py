from __future__ import annotations

import streamlit as st

from director_app.services import save_prompt_queue_item
from director_app.state import get_project, rebuild_prompt_queue, set_project
from director_app.ui_workbench.common import copy_box, section_title


def render(session_state: dict) -> None:
    project = get_project(session_state)
    queue = rebuild_prompt_queue(project)
    set_project(session_state, project)

    section_title("成片", "这里是 Prompt Queue，用于查看、编辑、复制视频提示词。")
    if not queue:
        st.info("还没有视频提示词。请先到「分镜」页生成视频提示词。")
        return

    labels = [
        f"{idx + 1}. {item.get('scene') or 'Scene'} · {item.get('from_beat')} → {item.get('to_beat')}"
        for idx, item in enumerate(queue)
    ]
    idx = st.selectbox("Prompt", range(len(queue)), format_func=lambda i: labels[i])
    item = queue[idx]

    cols = st.columns(4)
    cols[0].metric("scene", item.get("scene", ""))
    cols[1].metric("from", item.get("from_beat", ""))
    cols[2].metric("to", item.get("to_beat", ""))
    cols[3].metric("duration", item.get("duration_hint", ""))

    prompt = st.text_area("video_prompt", value=item.get("video_prompt", ""), height=260)
    dialogue = st.text_area("dialogue", value=item.get("dialogue", ""), height=90, disabled=True)
    status = st.selectbox(
        "status",
        ["draft", "ready", "edited"],
        index=["draft", "ready", "edited"].index(item.get("status", "draft"))
        if item.get("status", "draft") in ["draft", "ready", "edited"]
        else 0,
    )
    if st.button("保存修改"):
        next_project = save_prompt_queue_item(project, idx, prompt, status)
        set_project(session_state, next_project)
        st.success("已保存。")

    copy_box("复制用视频提示词", prompt, height=180)
    if dialogue:
        st.caption("上方 dialogue 只作为成片参考，不参与当前复制框自动拼接。")
