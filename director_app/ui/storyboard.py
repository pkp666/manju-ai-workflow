from __future__ import annotations

import streamlit as st

from director_app.project import beat_status, grouped_beats
from director_app.ui.common import section_title, status_chip


def render(state: dict) -> None:
    section_title("分镜工作台", "以 Shot/Beat 为中心管理镜头字段、关键帧和返工状态。")

    groups = grouped_beats(state)
    if not groups:
        st.info("还没有镜头计划。")
        return

    scene_ids = sorted(groups)
    selected_scene = st.selectbox("Scene", scene_ids, format_func=lambda sid: f"Scene {sid}")
    beats = groups[selected_scene]
    beat_ids = [b.get("beat_id", "") for b in beats]
    selected_beat_id = st.selectbox("Shot", beat_ids)
    beat = next((b for b in beats if b.get("beat_id") == selected_beat_id), beats[0])

    left, center, right = st.columns([1, 1.4, 1])
    with left:
        st.markdown("**镜头列表**")
        for item in beats:
            bid = item.get("beat_id", "")
            status = beat_status(state, bid)
            st.caption(
                f"{bid}  "
                f"{'F' if status['frame'] else '-'}"
                f"{'V' if status['video'] else '-'}"
            )

    with center:
        st.markdown(f"**{beat.get('beat_id', '')} · {beat.get('type', '')}**")
        st.write(beat.get("raw_text", ""))
        frame = _frame_for_beat(state, beat.get("beat_id", ""))
        if frame:
            st.image(frame, caption="关键帧", use_container_width=True)
        else:
            st.info("暂无关键帧。旧 Tab5/Tab6 的生成和转换会迁移到这里。")

    with right:
        st.markdown("**生产状态**")
        status = beat_status(state, beat.get("beat_id", ""))
        st.write(status_chip("镜头字段", status["shot"]))
        st.write(status_chip("关键帧", status["frame"]))
        st.write(status_chip("视频片段", status["video"]))
        st.divider()
        st.markdown("**字段**")
        st.json(state.get("shot_fields", {}).get(beat.get("beat_id", ""), {}))


def _frame_for_beat(state: dict, beat_id: str) -> str | None:
    for grid in state.get("grid_state", {}).values():
        if not isinstance(grid, dict):
            continue
        beat_ids = grid.get("beat_ids", [])
        if beat_id not in beat_ids:
            continue
        cell_num = beat_ids.index(beat_id) + 1
        locked_cells = {int(k): v for k, v in grid.get("locked_cells", {}).items()}
        return locked_cells.get(cell_num)
    return None
