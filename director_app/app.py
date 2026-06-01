from __future__ import annotations

import streamlit as st

from director_app.ui import assets, dashboard, render, script, storyboard
from state.project_io import (
    build_project_from_state,
    has_project_data,
    load_project_file,
    load_project_into_state,
    project_json,
)


st.set_page_config(page_title="AI 漫剧导演工作台", layout="wide")


def main() -> None:
    st.title("AI 漫剧导演工作台")
    st.caption("新的信息架构实验版：总览 / 剧本 / 资产 / 分镜 / 成片。")

    with st.sidebar:
        st.markdown("### 项目")
        uploaded = st.file_uploader("导入旧 project.json", type=["json"])
        if uploaded:
            file_id = f"{uploaded.name}_{uploaded.size}"
            if st.session_state.get("_director_loaded_id") != file_id:
                try:
                    stats = load_project_into_state(
                        load_project_file(uploaded),
                        st.session_state,
                        file_id,
                    )
                    st.session_state["_director_loaded_id"] = file_id
                    st.success(
                        f"已导入：{stats['beats']} shots · "
                        f"{stats['chars']} characters · {stats['scenes']} scenes"
                    )
                    if stats["missing_images"]:
                        st.warning(f"{stats['missing_images']} 张本地图片缺失，已重置")
                    st.rerun()
                except Exception as exc:
                    st.error(f"导入失败：{exc}")

        if has_project_data(st.session_state):
            project = build_project_from_state(st.session_state)
            st.download_button(
                "导出 project.json",
                data=project_json(project),
                file_name="project.json",
                mime="application/json",
                use_container_width=True,
            )
        else:
            st.info("先导入旧项目，或后续在这里新建项目。")

        st.divider()
        st.markdown("### 导航说明")
        st.caption("这版先验证产品结构，不替代旧 app.py 的完整生产能力。")

    pages = {
        "总览": dashboard.render,
        "剧本": script.render,
        "资产": assets.render,
        "分镜": storyboard.render,
        "成片": render.render,
    }
    selected = st.tabs(list(pages.keys()))
    for tab, (_, render_page) in zip(selected, pages.items()):
        with tab:
            render_page(st.session_state)


if __name__ == "__main__":
    main()
