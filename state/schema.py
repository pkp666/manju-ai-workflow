"""Default state shapes used by project load/save migrations."""

CARD_DEFAULTS = {
    "status": "pending",
    "gene": "",
    "expression": "",
    "prompt": "",
    "prompts": ["", "", ""],
    "candidates": [],
    "selected": None,
    "locked_img": None,
    "generation": 0,
    "history": [],
}

APPEARANCE_DEFAULTS = {
    "gender": "female",
    "age_desc": "",
    "role_en": "",
    "features": "",
    "trait": "",
    "physique": "",
    "hairstyle": "",
    "outfit": "",
    "makeup": "",
}

SHOT_FIELD_DEFAULTS = {
    "status": "done",
    "shot_scale": "",
    "camera_angle": "",
    "lighting": "",
    "mood": "",
    "subject": "",
    "rationale": "",
    "reasoning": "",
    "step3_checks": {},
    "layer2_note": "",
    "audio_type": "",
    "energy_type": "Neutral",
    "motion_vector": "static",
    "dialogue": "",
    "dialogue_emotion": "",
    "duration_hint": 0,
}

GRID_DEFAULTS = {
    "beat_ids": [],
    "panel_descs": [],
    "grid_prompt": "",
    "candidates": [],
    "selected": None,
    "selected_cells": [],
    "locked_img": None,
    "locked_cells": {},
    "generation": 0,
    "history": [],
    "status": "pending",
    "ref_notes": "",
    "l1_summary": "",
    "char_ref_map": {},
}

VIDEO_STATE_DEFAULTS = {
    "seg_id": 0,
    "from_beat_id": "",
    "to_beat_id": "",
    "from_img": None,
    "to_img": None,
    "pair_idx": 0,
    "video_prompt": "",
    "dialogue": "",
    "dialogue_emotion": "",
    "audio_type": "",
    "motion": "",
    "duration_hint": "",
    "draft_lens": "",
    "draft_motion": "",
    "draft_cont": "",
    "video_path": None,
    "status": "pending",
}

RS_DEFAULTS = {
    "raw": "",
    "tone_qa": [],
    "tone_current": None,
    "tone_text": "",
    "tone_confirmed": False,
    "refine_qa": [],
    "refine_current": None,
    "refined_script": "",
    "script_confirmed": False,
}

PROJECT_FIELDS = [
    "story_tone",
    "raw_script",
    "beats",
    "grid_map",
    "style_config",
    "chars_init",
    "appearance_init",
    "chars",
    "char_assets",
    "scene_assets",
    "props_init",
    "prop_assets",
    "shot_fields",
    "grid_state",
    "video_state",
    "scenes",
    "visual_aliases",
]

HAS_DATA_KEYS = [
    "story_tone",
    "scenes",
    "beats",
    "chars",
    "char_assets",
    "scene_assets",
    "style_config",
]
