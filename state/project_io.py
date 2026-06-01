"""Project save/load helpers for the Streamlit app."""

from __future__ import annotations

import json
from typing import Any

from .migrations import apply_project_to_state, migrate_project
from .schema import HAS_DATA_KEYS


def has_project_data(session_state: dict[str, Any]) -> bool:
    return any(session_state.get(key) for key in HAS_DATA_KEYS)


def build_project_from_state(session_state: dict[str, Any]) -> dict[str, Any]:
    rs = session_state.get("rs", {})
    return {
        "story_tone": session_state.get("story_tone", ""),
        "raw_script": session_state.get("raw_script", ""),
        "chars_init": session_state.get("chars_init", {}),
        "props_init": session_state.get("props_init", []),
        "scenes": session_state.get("scenes", []),
        "appearance_init": session_state.get("appearance_init", {}),
        "rs": {
            "raw": rs.get("raw", ""),
            "tone_text": rs.get("tone_text", ""),
            "tone_confirmed": rs.get("tone_confirmed", False),
            "script_confirmed": rs.get("script_confirmed", False),
            "segment_structure": rs.get("segment_structure", {}),
            "refine_qa": rs.get("refine_qa", []),
            "refine_confirmed": rs.get("refine_confirmed", False),
            "segments": rs.get("segments", []),
            "drama_segments": rs.get("drama_segments", []),
            "scenes": rs.get("scenes", []),
        },
        "beats": session_state.get("beats", []),
        "grid_map": session_state.get("grid_map", {}),
        "style_config": session_state.get("style_config", {}),
        "chars": session_state.get("chars", []),
        "char_assets": session_state.get("char_assets", {}),
        "visual_aliases": session_state.get("visual_aliases", {}),
        "scene_assets": session_state.get("scene_assets", {}),
        "prop_assets": session_state.get("prop_assets", {}),
        "shot_fields": session_state.get("shot_fields", {}),
        "grid_state": session_state.get("grid_state", {}),
        "extra_text": session_state.get("extra_text_saved", ""),
        "video_state": session_state.get("video_state", {}),
    }


def project_json(project: dict[str, Any]) -> str:
    return json.dumps(project, ensure_ascii=False, indent=2)


def load_project_file(uploaded_file: Any) -> dict[str, Any]:
    return json.load(uploaded_file)


def load_project_into_state(
    project: dict[str, Any],
    session_state: dict[str, Any],
    file_id: str,
) -> dict[str, int]:
    project, missing = migrate_project(project)
    apply_project_to_state(project, session_state, file_id)
    return {
        "beats": len(project.get("beats", [])),
        "chars": len(project.get("chars", [])),
        "scenes": len(project.get("scene_assets", {})),
        "missing_images": len(missing),
    }
