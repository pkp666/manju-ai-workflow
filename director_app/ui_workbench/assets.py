from __future__ import annotations

import streamlit as st

from director_app.state import get_project, set_project
from director_app.ui_workbench.common import section_title


BUCKET_LABELS = {
    "characters": "Character",
    "environments": "Environment",
    "props": "Prop",
    "style": "Style",
}


def render(session_state: dict) -> None:
    project = get_project(session_state)
    section_title("资产", "第一版只管理 References，不调用生图。")

    refs = project.get("references", {})
    if not any(refs.get(bucket) for bucket in BUCKET_LABELS):
        st.info("自动拆剧本后会在这里生成 Character / Environment / Prop / Style references。")
        return

    for bucket, label in BUCKET_LABELS.items():
        items = refs.get(bucket, {})
        st.markdown(f"### {label}")
        if not items:
            st.caption("暂无")
            continue
        for name, ref in items.items():
            with st.expander(f"{name} · {ref.get('type', label.lower())}", expanded=False):
                st.write(ref.get("description", ""))
                note = st.text_area(
                    "可编辑备注",
                    value=ref.get("note", ""),
                    key=f"ref_note_{bucket}_{name}",
                    height=90,
                )
                ref_image = st.text_input(
                    "参考图路径/URL（可选）",
                    value=ref.get("ref_image", ""),
                    key=f"ref_img_{bucket}_{name}",
                )
                used = ", ".join(ref.get("used_in_beats", [])[:30])
                st.caption(f"用于 beats: {used or '暂无'}")
                if st.button("保存 Reference", key=f"save_ref_{bucket}_{name}"):
                    project["references"][bucket][name]["note"] = note
                    project["references"][bucket][name]["ref_image"] = ref_image
                    set_project(session_state, project)
                    st.success("已保存。")
