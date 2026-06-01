"""Compatibility patches for loaded project JSON."""

from __future__ import annotations

import copy
import os
from typing import Any

from .schema import (
    APPEARANCE_DEFAULTS,
    CARD_DEFAULTS,
    GRID_DEFAULTS,
    PROJECT_FIELDS,
    RS_DEFAULTS,
    SHOT_FIELD_DEFAULTS,
    VIDEO_STATE_DEFAULTS,
)


def patch_card(state: dict[str, Any]) -> None:
    for key, value in CARD_DEFAULTS.items():
        if key not in state:
            state[key] = copy.deepcopy(value)


def patch_char(state: dict[str, Any]) -> None:
    if "base" not in state:
        base = {key: state.pop(key) for key in list(CARD_DEFAULTS) if key in state}
        state["base"] = base
    patch_card(state["base"])
    state.setdefault("extra_note", "")
    state.setdefault("periods", {})
    state.setdefault("speech_style", "")
    state.setdefault("dialogue_samples", [])
    state.setdefault("appearance", copy.deepcopy(APPEARANCE_DEFAULTS))
    for period_state in state["periods"].values():
        patch_card(period_state)


def patch_scene(state: dict[str, Any]) -> None:
    patch_card(state)
    state.setdefault("scene_card", {})
    state.setdefault("scene_props", [])
    state.setdefault("sub_scenes", {})
    for sub_state in state["sub_scenes"].values():
        patch_card(sub_state)


def patch_shot_fields(shot_fields: dict[str, Any]) -> dict[str, Any]:
    for fields in shot_fields.values():
        if not isinstance(fields, dict):
            continue
        if fields.get("status") in ("skip", "pending"):
            continue
        for key, value in SHOT_FIELD_DEFAULTS.items():
            if key not in fields:
                fields[key] = copy.deepcopy(value)
    return shot_fields


def patch_grid_state(grid_state: dict[Any, Any]) -> dict[Any, Any]:
    _convert_int_keys(grid_state)
    for grid in grid_state.values():
        if not isinstance(grid, dict):
            continue
        for key, value in GRID_DEFAULTS.items():
            if key not in grid:
                grid[key] = copy.deepcopy(value)
        if grid.get("locked_cells"):
            grid["locked_cells"] = {
                int(key): value for key, value in grid["locked_cells"].items()
            }
        path = grid.get("locked_img")
        if path and not _path_exists_or_remote(path):
            grid["locked_img"] = None
            grid["locked_cells"] = {}
            grid["status"] = "selecting" if any(grid.get("candidates", [])) else "pending"
        grid["candidates"] = [
            item for item in grid.get("candidates", [])
            if item and _path_exists_or_remote(item)
        ]
        grid["locked_cells"] = {
            key: value for key, value in grid.get("locked_cells", {}).items()
            if value and _path_exists_or_remote(value)
        }
        if not grid.get("rows") or not grid.get("cols"):
            rows, cols = _grid_shape(len(grid.get("beat_ids", [])))
            grid["rows"], grid["cols"] = rows, cols
            grid["total"] = rows * cols
        if grid.get("status") == "locked" and not grid.get("locked_img"):
            grid["status"] = "selecting" if grid.get("candidates") else "pending"
    return grid_state


def patch_video_state(video_state: dict[str, Any]) -> dict[str, Any]:
    for state in video_state.values():
        if not isinstance(state, dict):
            continue
        for key, value in VIDEO_STATE_DEFAULTS.items():
            if key not in state:
                state[key] = copy.deepcopy(value)
    return video_state


def migrate_project(project: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Patch a loaded project in place and return missing local images."""
    for char_state in project.get("char_assets", {}).values():
        patch_char(char_state)
    for scene_state in project.get("scene_assets", {}).values():
        patch_scene(scene_state)

    if project.get("shot_fields"):
        project["shot_fields"] = patch_shot_fields(project["shot_fields"])
    if project.get("grid_state"):
        project["grid_state"] = patch_grid_state(project["grid_state"])
    if project.get("video_state"):
        project["video_state"] = patch_video_state(project["video_state"])

    missing = _clear_missing_asset_images(project)
    return project, missing


def apply_project_to_state(project: dict[str, Any], session_state: dict[str, Any], file_id: str) -> None:
    for key in PROJECT_FIELDS:
        if project.get(key) is not None:
            session_state[key] = project[key]

    if "grid_map" in session_state:
        _convert_int_keys(session_state["grid_map"])

    if project.get("rs"):
        saved_rs = project["rs"]
        session_state.setdefault("rs", copy.deepcopy(RS_DEFAULTS))
        session_state["rs"].update({
            "raw": saved_rs.get("raw", ""),
            "tone_text": saved_rs.get("tone_text", ""),
            "tone_confirmed": saved_rs.get("tone_confirmed", False),
            "script_confirmed": saved_rs.get("script_confirmed", False),
            "segment_structure": saved_rs.get("segment_structure", {}),
            "refine_qa": saved_rs.get("refine_qa", []),
            "refine_confirmed": saved_rs.get("refine_confirmed", False),
            "segments": saved_rs.get("segments", []),
            "drama_segments": saved_rs.get("drama_segments", []),
            "scenes": saved_rs.get("scenes", []),
        })

    if project.get("extra_text"):
        session_state["extra_text_saved"] = project["extra_text"]

    session_state["_loaded_proj_id"] = file_id


def _clear_missing_asset_images(project: dict[str, Any]) -> list[str]:
    missing = []
    for char_state in project.get("char_assets", {}).values():
        base = char_state.get("base", {})
        image = base.get("locked_img")
        if image and not _path_exists_or_remote(image):
            missing.append(image)
            base["locked_img"] = None
            base["status"] = "pending"
        for period_state in char_state.get("periods", {}).values():
            image = period_state.get("locked_img")
            if image and not _path_exists_or_remote(image):
                missing.append(image)
                period_state["locked_img"] = None
                period_state["status"] = "pending"

    for scene_state in project.get("scene_assets", {}).values():
        image = scene_state.get("locked_img")
        if image and not _path_exists_or_remote(image):
            missing.append(image)
            scene_state["locked_img"] = None
            scene_state["status"] = "pending"
        for sub_state in scene_state.get("sub_scenes", {}).values():
            image = sub_state.get("locked_img")
            if image and not _path_exists_or_remote(image):
                missing.append(image)
                sub_state["locked_img"] = None
                sub_state["status"] = "pending"
    return missing


def _convert_int_keys(data: dict[Any, Any]) -> None:
    str_keys = [key for key in data if isinstance(key, str)]
    for key in str_keys:
        try:
            data[int(key)] = data.pop(key)
        except (ValueError, KeyError):
            pass


def _path_exists_or_remote(path: Any) -> bool:
    if not path:
        return True
    if str(path).startswith("http"):
        return True
    return os.path.exists(str(path))


def _grid_shape(count: int) -> tuple[int, int]:
    if count <= 4:
        return 2, 2
    if count <= 9:
        return 3, 3
    if count <= 16:
        return 4, 4
    return 5, 5
