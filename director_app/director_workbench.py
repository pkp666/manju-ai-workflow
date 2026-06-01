from __future__ import annotations

import streamlit as st

from director_app.state import DEFAULT_STYLE_SUFFIX, get_project, reset_project, set_project
from director_app.ui_workbench import assets, dashboard, final_cut, script, storyboard


st.set_page_config(page_title="AI 漫剧导演工作台", layout="wide")


def main() -> None:
    project = get_project(st.session_state)

    st.title("AI 漫剧导演工作台")
    st.caption("自动拆剧本、管理 references，并生成可复制的图片 / 宫格 / 视频提示词。")

    with st.sidebar:
        st.markdown("### 新项目")
        title = st.text_input("项目标题", value=project.get("title", ""))
        story_tone = st.text_area("故事论调", value=project.get("story_tone", ""), height=110)
        style_suffix = st.text_area(
            "风格后缀",
            value=project.get("style_suffix") or DEFAULT_STYLE_SUFFIX,
            height=110,
        )
        raw_script = st.text_area("原始剧本", value=project.get("raw_script", ""), height=260)

        col_a, col_b = st.columns(2)
        with col_a:
            if st.button("创建/更新", use_container_width=True):
                project.update({
                    "title": title,
                    "story_tone": story_tone,
                    "style_suffix": style_suffix or DEFAULT_STYLE_SUFFIX,
                    "raw_script": raw_script,
                })
                set_project(st.session_state, project)
                st.rerun()
        with col_b:
            if st.button("重置", use_container_width=True):
                reset_project(st.session_state)
                st.rerun()

        st.divider()
        st.caption("新版入口不导入旧 project.json，也不直接生成图片或视频。")

    pages = {
        "总览": dashboard.render,
        "剧本": script.render,
        "资产": assets.render,
        "分镜": storyboard.render,
        "成片": final_cut.render,
    }
    tabs = st.tabs(list(pages.keys()))
    for tab, render_page in zip(tabs, pages.values()):
        with tab:
            render_page(st.session_state)


if __name__ == "__main__":
    main()
