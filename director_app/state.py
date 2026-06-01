"""State helpers for the new director workbench.

This module is intentionally independent from the legacy project.json schema.
The new UI reads and writes ``st.session_state["director_project"]`` first,
then mirrors a few legacy keys only so existing generation modules can run.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


DEFAULT_STYLE_SUFFIX = "best quality, cinematic comic style"
PROJECT_KEY = "director_project"


def empty_project(
    title: str = "",
    raw_script: str = "",
    story_tone: str = "",
    style_suffix: str = DEFAULT_STYLE_SUFFIX,
) -> dict[str, Any]:
    return {
        "title": title or "未命名项目",
        "raw_script": raw_script,
        "story_tone": story_tone,
        "style_suffix": style_suffix or DEFAULT_STYLE_SUFFIX,
        "chars_init": {},
        "appearance_init": {},
        "props_init": [],
        "rs": {
            "segment_structure": {},
            "segments": [],
            "drama_segments": [],
        },
        "scenes": [],
        "beats": [],
        "grid_map": {},
        "prop_assets": [],
        "style_config": {
            "suffix": style_suffix or DEFAULT_STYLE_SUFFIX,
            "char_suffix": style_suffix or DEFAULT_STYLE_SUFFIX,
            "scene_suffix": style_suffix or DEFAULT_STYLE_SUFFIX,
        },
        "references": {
            "characters": {},
            "environments": {},
            "props": {},
            "style": {
                "global": {
                    "name": "global",
                    "type": "style",
                    "description": style_suffix or DEFAULT_STYLE_SUFFIX,
                    "note": "",
                    "ref_image": "",
                    "used_in_beats": [],
                }
            },
        },
        "shot_fields": {},
        "panel_prompts": {},
        "video_prompts": {},
        "prompt_queue": [],
    }


def get_project(session_state: dict[str, Any]) -> dict[str, Any]:
    if PROJECT_KEY not in session_state:
        session_state[PROJECT_KEY] = empty_project()
    project = session_state[PROJECT_KEY]
    ensure_project_shape(project)
    return project


def set_project(session_state: dict[str, Any], project: dict[str, Any]) -> dict[str, Any]:
    ensure_project_shape(project)
    session_state[PROJECT_KEY] = project
    sync_legacy_state(session_state)
    return project


def update_project(session_state: dict[str, Any], **fields: Any) -> dict[str, Any]:
    project = deepcopy(get_project(session_state))
    project.update(fields)
    return set_project(session_state, project)


def reset_project(
    session_state: dict[str, Any],
    title: str = "",
    raw_script: str = "",
    story_tone: str = "",
    style_suffix: str = DEFAULT_STYLE_SUFFIX,
) -> dict[str, Any]:
    return set_project(
        session_state,
        empty_project(title, raw_script, story_tone, style_suffix),
    )


def ensure_project_shape(project: dict[str, Any]) -> None:
    defaults = empty_project(
        project.get("title", ""),
        project.get("raw_script", ""),
        project.get("story_tone", ""),
        project.get("style_suffix") or DEFAULT_STYLE_SUFFIX,
    )
    for key, value in defaults.items():
        project.setdefault(key, value)
    project["style_suffix"] = project.get("style_suffix") or DEFAULT_STYLE_SUFFIX
    project["style_config"] = project.get("style_config") or defaults["style_config"]
    project["references"] = project.get("references") or defaults["references"]
    for bucket in ("characters", "environments", "props", "style"):
        project["references"].setdefault(bucket, {})


def sync_legacy_state(session_state: dict[str, Any]) -> None:
    project = session_state.get(PROJECT_KEY) or empty_project()
    session_state["raw_script"] = project.get("raw_script", "")
    session_state["story_tone"] = project.get("story_tone", "")
    session_state["chars_init"] = project.get("chars_init", {})
    session_state["appearance_init"] = project.get("appearance_init", {})
    session_state["props_init"] = project.get("props_init", [])
    session_state["rs"] = project.get("rs", {})
    session_state["scenes"] = project.get("scenes", [])
    session_state["beats"] = project.get("beats", [])
    session_state["grid_map"] = project.get("grid_map", {})
    session_state["prop_assets"] = project.get("prop_assets", [])
    session_state["style_config"] = project.get("style_config", {})
    session_state["shot_fields"] = project.get("shot_fields", {})


def build_references(
    chars_init: dict[str, Any],
    appearance_init: dict[str, Any],
    props_init: list[dict[str, Any]],
    scenes: list[dict[str, Any]],
    beats: list[dict[str, Any]],
    style_suffix: str,
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    existing = existing or {}
    references = {
        "characters": {},
        "environments": {},
        "props": {},
        "style": deepcopy(existing.get("style") or {}),
    }

    for name, info in (chars_init or {}).items():
        old = (existing.get("characters") or {}).get(name, {})
        appearance = (appearance_init or {}).get(name, {})
        desc_parts = [
            info.get("role", ""),
            appearance.get("gender", ""),
            appearance.get("age_desc", ""),
            appearance.get("role_en", ""),
            appearance.get("features", ""),
            appearance.get("trait", ""),
            appearance.get("physique", ""),
            appearance.get("hairstyle", ""),
            appearance.get("outfit", ""),
            appearance.get("makeup", ""),
        ]
        references["characters"][name] = {
            "name": name,
            "type": "character",
            "description": " / ".join([str(x) for x in desc_parts if x]),
            "note": old.get("note", ""),
            "ref_image": old.get("ref_image", ""),
            "used_in_beats": _beats_for_character(beats, name),
        }

    for scene in scenes or []:
        name = (
            scene.get("scene_name")
            or scene.get("name")
            or f"Scene {scene.get('scene_id', len(references['environments']) + 1)}"
        )
        old = (existing.get("environments") or {}).get(name, {})
        references["environments"][name] = {
            "name": name,
            "type": "environment",
            "description": scene.get("space_desc") or scene.get("scene_text", "")[:260],
            "note": old.get("note", ""),
            "ref_image": old.get("ref_image", ""),
            "used_in_beats": _beats_for_scene(beats, scene),
        }

    for prop in props_init or []:
        name = prop.get("name")
        if not name:
            continue
        old = (existing.get("props") or {}).get(name, {})
        references["props"][name] = {
            "name": name,
            "type": "prop",
            "description": prop.get("description", ""),
            "note": old.get("note", ""),
            "ref_image": old.get("ref_image", ""),
            "used_in_beats": _beats_for_prop(beats, name),
        }

    references["style"]["global"] = {
        "name": "global",
        "type": "style",
        "description": style_suffix or DEFAULT_STYLE_SUFFIX,
        "note": references["style"].get("global", {}).get("note", ""),
        "ref_image": references["style"].get("global", {}).get("ref_image", ""),
        "used_in_beats": [b.get("beat_id", "") for b in beats or [] if b.get("beat_id")],
    }
    return references


def grouped_beats(project: dict[str, Any]) -> dict[int, list[dict[str, Any]]]:
    groups: dict[int, list[dict[str, Any]]] = {}
    for beat in project.get("beats", []):
        sid = int(beat.get("segment") or beat.get("scene_id") or 0)
        groups.setdefault(sid, []).append(beat)
    return groups


def project_counts(project: dict[str, Any]) -> dict[str, int]:
    refs = project.get("references", {})
    return {
        "scenes": len(project.get("scenes", [])),
        "beats": len(project.get("beats", [])),
        "characters": len(refs.get("characters", {})),
        "environments": len(refs.get("environments", {})),
        "props": len(refs.get("props", {})),
        "shot_fields": len(project.get("shot_fields", {})),
        "panel_prompts": len(project.get("panel_prompts", {})),
        "video_prompts": len(project.get("video_prompts", {})),
        "queue": len(project.get("prompt_queue", [])),
    }


def reference_assets(project: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    refs = project.get("references", {})
    char_assets = {}
    for name, ref in refs.get("characters", {}).items():
        img = ref.get("ref_image", "")
        char_assets[name] = {"base": {"locked_img": img} if img else {}, "periods": {}}

    scene_assets = {}
    for name, ref in refs.get("environments", {}).items():
        scene_assets[name] = {"locked_img": ref.get("ref_image", "")}
    return char_assets, scene_assets


def upsert_reference(
    session_state: dict[str, Any],
    bucket: str,
    name: str,
    note: str,
    ref_image: str,
) -> dict[str, Any]:
    project = deepcopy(get_project(session_state))
    ref = project["references"].setdefault(bucket, {}).setdefault(name, {})
    ref["note"] = note
    ref["ref_image"] = ref_image
    return set_project(session_state, project)


def rebuild_prompt_queue(project: dict[str, Any]) -> list[dict[str, Any]]:
    queue = []
    for beat_id, item in (project.get("video_prompts") or {}).items():
        if str(beat_id).startswith("__"):
            continue
        if not isinstance(item, dict):
            continue
        queue.append({
            "scene": item.get("scene") or _scene_for_pair(project, item),
            "from_beat": item.get("from_beat_id", beat_id),
            "to_beat": item.get("to_beat_id", ""),
            "video_prompt": item.get("video_prompt", ""),
            "dialogue": item.get("dialogue", ""),
            "duration_hint": item.get("duration_hint", ""),
            "status": item.get("status", "draft"),
        })
    project["prompt_queue"] = queue
    return queue


def _beats_for_character(beats: list[dict[str, Any]], name: str) -> list[str]:
    return [
        b.get("beat_id", "")
        for b in beats or []
        if name in (b.get("characters") or []) and b.get("beat_id")
    ]


def _beats_for_scene(beats: list[dict[str, Any]], scene: dict[str, Any]) -> list[str]:
    scene_id = scene.get("scene_id")
    scene_name = scene.get("scene_name")
    return [
        b.get("beat_id", "")
        for b in beats or []
        if b.get("beat_id")
        and (b.get("segment") == scene_id or b.get("scene_name") == scene_name or b.get("scene") == scene_name)
    ]


def _beats_for_prop(beats: list[dict[str, Any]], name: str) -> list[str]:
    found = []
    for beat in beats or []:
        props = beat.get("props") or beat.get("prop_names") or []
        text = beat.get("raw_text", "")
        if name in props or (name and name in text):
            found.append(beat.get("beat_id", ""))
    return [x for x in found if x]


def _scene_for_pair(project: dict[str, Any], item: dict[str, Any]) -> str:
    beat_id = item.get("from_beat_id")
    for beat in project.get("beats", []):
        if beat.get("beat_id") == beat_id:
            return beat.get("scene_name") or beat.get("scene") or f"Scene {beat.get('segment', '')}"
    return ""
