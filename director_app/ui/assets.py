from __future__ import annotations

import streamlit as st

from director_app.ui.common import section_title


def render(state: dict) -> None:
    section_title("资产库", "统一管理 Character、Environment、Prop、Style 这些 References。")

    style = state.get("style_config", {})
    with st.expander("Style", expanded=True):
        if style:
            st.json(style)
        else:
            st.info("还没有风格配置。旧系统 Tab2 会迁移到这里。")

    tabs = st.tabs(["Character", "Environment", "Prop"])
    _render_asset_group(tabs[0], state.get("char_assets", {}), "base")
    _render_asset_group(tabs[1], state.get("scene_assets", {}), None)
    _render_asset_group(tabs[2], state.get("prop_assets", {}), None)


def _render_asset_group(tab, assets: dict, nested_card: str | None) -> None:
    with tab:
        if not assets:
            st.info("暂无资产。")
            return
        cols = st.columns(3)
        for idx, (name, asset) in enumerate(assets.items()):
            card = asset.get(nested_card, {}) if nested_card else asset
            with cols[idx % 3]:
                st.markdown(f"**{name}**")
                image = card.get("locked_img")
                if image:
                    st.image(image, use_container_width=True)
                else:
                    st.caption("未锁定参考图")
                st.caption(f"status: {card.get('status', 'pending')}")
