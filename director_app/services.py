"""Service layer for the prompt-first director workbench."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from director_app.state import (
    DEFAULT_STYLE_SUFFIX,
    build_references,
    rebuild_prompt_queue,
    reference_assets,
)


def auto_split_script(project: dict[str, Any]) -> dict[str, Any]:
    raw_script = (project.get("raw_script") or "").strip()
    if not raw_script:
        raise ValueError("请先粘贴剧本。")

    from script_analyzer import analyze_raw_script
    from script_parser import parse_script
    from script_refine import (
        analyze_segment_structure,
        dramatize_all_segments,
        split_all_scenes,
        split_by_segments,
    )

    next_project = deepcopy(project)
    story_tone = next_project.get("story_tone", "")
    style_suffix = next_project.get("style_suffix") or DEFAULT_STYLE_SUFFIX
    qa_pairs: list[dict[str, Any]] = []

    analysis = analyze_raw_script(raw_script, story_tone)
    chars_init = analysis.get("chars_init", {})
    appearance_init = analysis.get("appearance_init", {})
    props_init = analysis.get("props_init", [])
    char_names = analysis.get("char_names") or list(chars_init.keys())

    segment_structure = analyze_segment_structure(raw_script, story_tone, chars_init)
    segments = split_by_segments(raw_script, story_tone, qa_pairs, segment_structure)
    drama_segments = dramatize_all_segments(
        segments,
        story_tone,
        chars_init,
        qa_pairs,
        segment_structure,
    )
    scenes = split_all_scenes(drama_segments, chars_init=chars_init)
    beats, grid_map, prop_assets = parse_script(
        raw_script,
        story_tone=story_tone,
        char_names=char_names,
        scenes=scenes,
    )

    next_project.update({
        "raw_script": raw_script,
        "story_tone": story_tone,
        "style_suffix": style_suffix,
        "chars_init": chars_init,
        "appearance_init": appearance_init,
        "props_init": props_init,
        "rs": {
            "segment_structure": segment_structure,
            "segments": segments,
            "drama_segments": drama_segments,
        },
        "scenes": scenes,
        "beats": beats,
        "grid_map": grid_map,
        "prop_assets": prop_assets,
        "style_config": {
            "suffix": style_suffix,
            "char_suffix": style_suffix,
            "scene_suffix": style_suffix,
        },
    })
    next_project["references"] = build_references(
        chars_init,
        appearance_init,
        props_init,
        scenes,
        beats,
        style_suffix,
        existing=project.get("references", {}),
    )
    return next_project


def generate_shot_fields(project: dict[str, Any], seg_id: int) -> dict[str, Any]:
    from shot_optimizer import run_segment

    next_project = deepcopy(project)
    char_assets, scene_assets = reference_assets(next_project)
    result = run_segment(
        seg_id=seg_id,
        beats=next_project.get("beats", []),
        char_assets=char_assets,
        scene_assets=scene_assets,
        style_suffix=next_project.get("style_suffix") or DEFAULT_STYLE_SUFFIX,
        story_tone=next_project.get("story_tone", ""),
        refined_script=_refined_script(next_project),
        existing_shot_fields=next_project.get("shot_fields", {}),
        chars_init=next_project.get("chars_init", {}),
    )
    next_project.setdefault("shot_fields", {}).update(result)
    return next_project


def generate_grid_prompt(project: dict[str, Any], seg_id: int) -> dict[str, Any]:
    from grid_generator import (
        assemble_grid_prompt,
        build_panel_descs,
        collect_ref_images,
        init_grid_state,
    )
    from shot_optimizer import infer_narrative_phase

    next_project = deepcopy(project)
    seg_beats = _segment_beats(next_project, seg_id)
    if not seg_beats:
        raise ValueError(f"Scene {seg_id} 没有 beats。")

    shot_fields = next_project.get("shot_fields", {})
    grid_state = init_grid_state(
        seg_id,
        seg_beats,
        shot_fields,
        next_project.get("grid_map", {}),
    )
    char_assets, scene_assets = reference_assets(next_project)
    _, _, ref_notes, char_ref_map = collect_ref_images(seg_beats, char_assets, scene_assets)
    l1_summary = _l1_summary(next_project, seg_id)
    narrative_phase = infer_narrative_phase(seg_id, len(_segment_ids(next_project)))
    panel_descs = build_panel_descs(
        seg_id,
        seg_beats,
        shot_fields,
        ref_notes,
        next_project.get("style_suffix") or DEFAULT_STYLE_SUFFIX,
        l1_summary=l1_summary,
        narrative_phase=narrative_phase,
        rows=grid_state["rows"],
        cols=grid_state["cols"],
        char_ref_map=char_ref_map,
    )
    grid_prompt = assemble_grid_prompt(
        panel_descs,
        grid_state["rows"],
        grid_state["cols"],
        ref_notes,
        next_project.get("style_suffix") or DEFAULT_STYLE_SUFFIX,
        l1_summary=l1_summary,
    )
    grid_state.update({
        "panel_descs": panel_descs,
        "grid_prompt": grid_prompt,
        "ref_notes": ref_notes,
        "l1_summary": l1_summary,
        "status": "ready",
    })
    next_project.setdefault("panel_prompts", {})[str(seg_id)] = grid_state
    return next_project


def generate_video_prompts(project: dict[str, Any], seg_id: int) -> dict[str, Any]:
    from video_prompter import run_segment

    next_project = deepcopy(project)
    seg_beats = _segment_beats(next_project, seg_id)
    if len([b for b in seg_beats if b.get("type") != "caption"]) < 2:
        raise ValueError("视频提示词至少需要 2 个有效 beat。")

    panel_descs_map = {}
    for key, item in (next_project.get("panel_prompts") or {}).items():
        if isinstance(item, dict):
            panel_descs_map[int(key)] = item.get("panel_descs", [])

    char_assets, scene_assets = reference_assets(next_project)
    result = run_segment(
        seg_id=seg_id,
        beats=next_project.get("beats", []),
        shot_fields=next_project.get("shot_fields", {}),
        panel_descs_map=panel_descs_map,
        char_assets=char_assets,
        scene_assets=scene_assets,
        style_suffix=next_project.get("style_suffix") or DEFAULT_STYLE_SUFFIX,
        story_tone=next_project.get("story_tone", ""),
        refined_script=_refined_script(next_project),
        existing_results=next_project.get("video_prompts", {}),
    )
    for key, value in result.items():
        if isinstance(value, dict):
            value.setdefault("status", "draft")
            value.setdefault("scene", _scene_name(next_project, seg_id))
    next_project.setdefault("video_prompts", {}).update(result)
    rebuild_prompt_queue(next_project)
    return next_project


def save_prompt_queue_item(
    project: dict[str, Any],
    index: int,
    video_prompt: str,
    status: str,
) -> dict[str, Any]:
    next_project = deepcopy(project)
    queue = rebuild_prompt_queue(next_project)
    if index < 0 or index >= len(queue):
        return next_project
    item = queue[index]
    item["video_prompt"] = video_prompt
    item["status"] = status
    beat_key = item.get("from_beat")
    if beat_key in next_project.get("video_prompts", {}):
        next_project["video_prompts"][beat_key]["video_prompt"] = video_prompt
        next_project["video_prompts"][beat_key]["status"] = status
    rebuild_prompt_queue(next_project)
    return next_project


def _segment_beats(project: dict[str, Any], seg_id: int) -> list[dict[str, Any]]:
    return [b for b in project.get("beats", []) if int(b.get("segment") or 0) == int(seg_id)]


def _segment_ids(project: dict[str, Any]) -> set[int]:
    return {int(b.get("segment") or 0) for b in project.get("beats", [])}


def _l1_summary(project: dict[str, Any], seg_id: int) -> str:
    for seg in project.get("rs", {}).get("drama_segments", []):
        if int(seg.get("segment_id") or 0) == int(seg_id):
            return seg.get("segment_function") or seg.get("segment_name") or ""
    return ""


def _scene_name(project: dict[str, Any], seg_id: int) -> str:
    for scene in project.get("scenes", []):
        if int(scene.get("scene_id") or 0) == int(seg_id):
            return scene.get("scene_name") or f"Scene {seg_id}"
    return f"Scene {seg_id}"


def _refined_script(project: dict[str, Any]) -> str:
    lines = []
    for seg in project.get("rs", {}).get("drama_segments", []):
        lines.append(seg.get("drama_text") or seg.get("original_text") or "")
    return "\n\n".join([line for line in lines if line]) or project.get("raw_script", "")
