"""Read-only project summaries for the director workbench."""

from __future__ import annotations

from typing import Any


def project_counts(state: dict[str, Any]) -> dict[str, int]:
    beats = state.get("beats", [])
    shot_fields = state.get("shot_fields", {})
    grid_state = state.get("grid_state", {})
    video_state = state.get("video_state", {})

    frame_count = 0
    for grid in grid_state.values():
        if isinstance(grid, dict):
            frame_count += len([v for v in grid.get("locked_cells", {}).values() if v])

    done_videos = len([
        v for v in video_state.values()
        if isinstance(v, dict) and v.get("status") == "done"
    ])

    return {
        "beats": len(beats),
        "scenes": len(state.get("scenes", [])),
        "characters": len(state.get("chars", [])),
        "char_assets": len(state.get("char_assets", {})),
        "scene_assets": len(state.get("scene_assets", {})),
        "prop_assets": len(state.get("prop_assets", {})),
        "shot_fields": len([
            v for v in shot_fields.values()
            if isinstance(v, dict) and v.get("status") in ("done", "edited")
        ]),
        "frames": frame_count,
        "videos": done_videos,
    }


def completion_ratio(done: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return max(0.0, min(1.0, done / total))


def grouped_beats(state: dict[str, Any]) -> dict[int, list[dict[str, Any]]]:
    groups: dict[int, list[dict[str, Any]]] = {}
    for beat in state.get("beats", []):
        sid = beat.get("segment", 0)
        groups.setdefault(sid, []).append(beat)
    return groups


def beat_status(state: dict[str, Any], beat_id: str) -> dict[str, bool]:
    shot_fields = state.get("shot_fields", {})
    grid_state = state.get("grid_state", {})
    video_state = state.get("video_state", {})

    has_shot = (
        isinstance(shot_fields.get(beat_id), dict)
        and shot_fields[beat_id].get("status") in ("done", "edited")
    )

    has_frame = False
    for grid in grid_state.values():
        if not isinstance(grid, dict):
            continue
        beat_ids = grid.get("beat_ids", [])
        if beat_id not in beat_ids:
            continue
        cell_num = beat_ids.index(beat_id) + 1
        locked_cells = {int(k): v for k, v in grid.get("locked_cells", {}).items()}
        has_frame = bool(locked_cells.get(cell_num))
        break

    has_video = any(
        isinstance(video, dict)
        and video.get("status") == "done"
        and beat_id in (video.get("from_beat_id"), video.get("to_beat_id"))
        for video in video_state.values()
    )

    return {"shot": has_shot, "frame": has_frame, "video": has_video}


def next_actions(state: dict[str, Any]) -> list[str]:
    counts = project_counts(state)
    actions: list[str] = []
    if not state.get("raw_script") and not state.get("rs", {}).get("raw"):
        actions.append("导入或粘贴剧本")
    if not state.get("beats"):
        actions.append("生成分场和镜头计划")
    if not state.get("style_config"):
        actions.append("确认视觉风格")
    if counts["characters"] and counts["char_assets"] < counts["characters"]:
        actions.append("补齐角色参考图")
    if counts["beats"] and counts["shot_fields"] < counts["beats"]:
        actions.append("扩写镜头字段")
    if counts["beats"] and counts["frames"] < counts["beats"]:
        actions.append("生成关键帧")
    if counts["frames"] and not counts["videos"]:
        actions.append("生成视频片段")
    return actions or ["检查成片并导出"]
