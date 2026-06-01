"""
grid_generator.py — Tab5 宫格生图核心逻辑

宫格规格统一入口：
  _get_grid_shape(valid_beat_count) 是唯一计算入口
  init_grid_state 用它算好后存入 state
  build_panel_descs / assemble_grid_prompt 都从 state 取 rows/cols，不重复计算

公开接口：
  init_grid_state(seg_id, seg_beats, shot_fields, grid_map) -> dict
  collect_ref_images(seg_beats, char_assets, scene_assets) -> tuple
  build_panel_descs(seg_id, seg_beats, shot_fields, ref_notes, style_suffix,
                    l1_summary, narrative_phase, rows, cols) -> list[dict]
  evolve_panel_descs(grid_state, selected_cells, user_note, shot_fields,
                     seg_beats, ref_notes, style_suffix, l1_summary) -> list[dict]
  assemble_grid_prompt(panel_descs, rows, cols, ref_notes, style_suffix,
                       l1_summary) -> str
  split_grid(img_path, rows, cols, output_dir) -> dict[int, str]
  get_cell_num(row, col, cols) -> int
"""

import json
import os
import time

from llm import call_llm
from grid_generator_prompts import (
    PANEL_GEN_SYSTEM, PANEL_GEN_USER,
    PANEL_EVOLVE_SYSTEM, PANEL_EVOLVE_USER,
)


# ════════════════════════════════════════════════════════════
# 宫格规格：唯一计算入口
# ════════════════════════════════════════════════════════════

def _get_grid_shape(valid_beat_count: int) -> tuple[int, int]:
    """
    根据有效beat数量（shot/line，不含caption）返回 (rows, cols)。
    这是全局唯一的宫格规格计算入口，所有地方都调这个。
    """
    if valid_beat_count <= 4:  return (2, 2)
    if valid_beat_count <= 9:  return (3, 3)
    if valid_beat_count <= 16: return (4, 4)
    return (5, 5)


def get_grid_shape_for_beats(beat_count: int) -> tuple[int, int]:
    """公开接口，供外部调用"""
    return _get_grid_shape(beat_count)


# ════════════════════════════════════════════════════════════
# 状态初始化
# ════════════════════════════════════════════════════════════

def init_grid_state(
    seg_id: int,
    seg_beats: list,
    shot_fields: dict,
    grid_map: dict,
) -> dict:
    """
    初始化单个segment的宫格状态。

    关键：宫格规格以 valid_beats（shot/line，不含caption）数量为准。
    grid_map 里的 rows/cols 是按全部beat（含caption）算的，不能用。
    """
    valid_beats = [b for b in seg_beats if b.get("type") != "caption"]
    beat_ids    = [b["beat_id"] for b in valid_beats]

    # 唯一入口：以有效beat数量决定宫格规格
    rows, cols = _get_grid_shape(len(valid_beats))

    return {
        "seg_id":         seg_id,
        "rows":           rows,
        "cols":           cols,
        "total":          rows * cols,
        "beat_ids":       beat_ids,
        "panel_descs":    [],
        "grid_prompt":    "",
        "candidates":     [],
        "selected":       None,
        "selected_cells": [],
        "locked_img":     None,
        "locked_cells":   {},
        "generation":     0,
        "history":        [],
        "status":         "pending",
        "ref_notes":      "",
        "l1_summary":     "",
    }


# ════════════════════════════════════════════════════════════
# 参考图收集
# ════════════════════════════════════════════════════════════

def collect_ref_images(
    seg_beats: list,
    char_assets: dict,
    scene_assets: dict,
    visual_aliases: dict | None = None,
) -> tuple[list[str], str | None, str, dict]:

    visual_aliases = visual_aliases or {}

    chars_seen = []
    for b in seg_beats:
        for c in (b.get("characters") or []):
            if c not in chars_seen:
                chars_seen.append(c)

    ref_chars    = []
    ref_labels   = []
    char_ref_map = {}

    for c in chars_seen:
        asset = char_assets.get(c, {})

        # ── 优先取时期装扮图 ──────────────────────────────
        img = None

        # 从本segment的beats里找该角色的period
        period = None
        for b in seg_beats:
            pm = b.get("period_map") or {}
            if c in pm and pm[c]:
                period = pm[c]
                break

        if period:
            period_state = asset.get("periods", {}).get(period, {})
            if period_state.get("status") == "locked":
                img = period_state.get("locked_img")

        # fallback到base素装
        if not img:
            img = asset.get("base", {}).get("locked_img")

        alias = visual_aliases.get(c, c)

        if img and os.path.exists(str(img)):
            ref_chars.append(img)
            ref_num = len(ref_chars)
            char_ref_map[c] = {"ref_num": ref_num, "alias": alias}
            ref_labels.append(
                f"Reference image {ref_num}: {alias}"
                f" — match this character appearance exactly in all panels"
            )
        else:
            char_ref_map[c] = {"ref_num": None, "alias": alias}

    ref_scene       = None
    scene_ref_label = ""

    for b in seg_beats:
        scene_key = b.get("scene_name") or b.get("scene")
        if not scene_key:
            continue
        asset = scene_assets.get(scene_key, {})
        img   = asset.get("locked_img")
        if img and os.path.exists(str(img)):
            ref_scene = img
            scene_num = len(ref_chars) + 1
            scene_ref_label = (
                f"Reference image {scene_num}: scene environment [{scene_key}]"
                f" — use this background setting throughout all panels"
            )
            break

    all_labels = ref_labels + ([scene_ref_label] if scene_ref_label else [])
    ref_notes  = (
        "\n".join(all_labels)
        if all_labels
        else "（无已锁定参考图，模型自行生成）"
    )

    return ref_chars, ref_scene, ref_notes, char_ref_map


# ════════════════════════════════════════════════════════════
# Panel描述生成（第0代）
# ════════════════════════════════════════════════════════════

def build_panel_descs(
    seg_id: int,
    seg_beats: list,
    shot_fields: dict,
    ref_notes: str,
    style_suffix: str,
    l1_summary: str = "",
    narrative_phase: str = "",
    rows: int = 0,
    cols: int = 0,
    char_ref_map: dict | None = None,
) -> list[dict]:
    """
    调LLM生成所有Panel描述
    返回：[{panel_num, beat_id, desc}]

    rows/cols 必须从 grid_state 传入（init_grid_state 已算好），
    不传则兜底自算，但调用方应该始终传入以保证一致性。
    """
    valid_beats = [b for b in seg_beats if b.get("type") != "caption"]
    seg_desc    = seg_beats[0].get("segment_desc", "") if seg_beats else ""

    # 优先用传入的 rows/cols（来自 grid_state，和 init_grid_state 一致）
    if not rows or not cols:
        rows, cols = _get_grid_shape(len(valid_beats))

    beats_fields_text = _format_beats_fields(valid_beats, shot_fields, char_ref_map or {})

    prompt = (PANEL_GEN_USER
        .replace("{seg_desc}",        seg_desc)
        .replace("{narrative_phase}", narrative_phase or "")
        .replace("{rows}",            str(rows))
        .replace("{cols}",            str(cols))
        .replace("{total}",           str(rows * cols))
        .replace("{ref_notes}",       ref_notes)
        .replace("{l1_summary}",      l1_summary or "（无叙事分析摘要）")
        .replace("{style_suffix}",    style_suffix)
        .replace("{beats_fields}",    beats_fields_text)
    )

    raw    = call_llm(prompt, PANEL_GEN_SYSTEM)
    result = _parse_json_array(raw)

    panel_descs = []
    for i, beat in enumerate(valid_beats):
        panel_num = i + 1
        matched   = next(
            (item for item in result
             if item.get("panel_num") == panel_num
             or item.get("beat_id") == beat["beat_id"]),
            None,
        )
        panel_descs.append({
            "panel_num": panel_num,
            "beat_id":   beat["beat_id"],
            "desc":      (matched.get("desc", "")
                          if matched
                          else _fallback_desc(beat, shot_fields, style_suffix)),
        })

    return panel_descs


# ════════════════════════════════════════════════════════════
# Panel描述进化
# ════════════════════════════════════════════════════════════

def evolve_panel_descs(
    grid_state: dict,
    selected_cells: list[int],
    user_note: str,
    shot_fields: dict,
    seg_beats: list,
    ref_notes: str,
    style_suffix: str,
    l1_summary: str = "",
    char_ref_map: dict | None = None,
) -> list[dict]:
    """
    只重写被勾选格子的Panel描述，其余原文完整保留
    返回：完整的新panel_descs列表
    """
    current_descs = grid_state["panel_descs"]
    rows          = grid_state["rows"]
    cols          = grid_state["cols"]
    seg_desc      = seg_beats[0].get("segment_desc", "") if seg_beats else ""

    current_descs_text = "\n".join(
        f"Panel {d['panel_num']} (beat {d['beat_id']}): {d['desc']}"
        for d in current_descs
    )

    selected_beat_ids    = [
        d["beat_id"] for d in current_descs
        if d["panel_num"] in selected_cells
    ]
    selected_beats       = [b for b in seg_beats if b["beat_id"] in selected_beat_ids]
    selected_fields_text = _format_beats_fields(selected_beats, shot_fields, char_ref_map or {})

    prompt = (PANEL_EVOLVE_USER
        .replace("{seg_desc}",              seg_desc)
        .replace("{rows}",                  str(rows))
        .replace("{cols}",                  str(cols))
        .replace("{ref_notes}",             ref_notes)
        .replace("{l1_summary}",            l1_summary or "（无叙事分析摘要）")
        .replace("{style_suffix}",          style_suffix)
        .replace("{current_descs}",         current_descs_text)
        .replace("{selected_cells}",        str(selected_cells))
        .replace("{user_note}",             user_note or "无，按导演判断进化")
        .replace("{selected_beats_fields}", selected_fields_text)
    )

    raw     = call_llm(prompt, PANEL_EVOLVE_SYSTEM)
    evolved = _parse_json_array(raw)

    evolved_map = {item["panel_num"]: item for item in evolved}
    new_descs   = []
    for d in current_descs:
        pnum = d["panel_num"]
        if pnum in selected_cells and pnum in evolved_map:
            new_descs.append({
                "panel_num":      pnum,
                "beat_id":        d["beat_id"],
                "desc":           evolved_map[pnum].get("desc", d["desc"]),
                "evolution_note": evolved_map[pnum].get("evolution_note", ""),
            })
        else:
            new_descs.append(d)

    return new_descs


# ════════════════════════════════════════════════════════════
# 宫格提示词组装
# ════════════════════════════════════════════════════════════

def assemble_grid_prompt(
    panel_descs: list[dict],
    rows: int,
    cols: int,
    ref_notes: str,
    style_suffix: str,
    l1_summary: str = "",
) -> str:
    """把所有Panel描述组装成完整的宫格生图提示词"""
    total = rows * cols

    # 严格等比宫格约束
    header = (
        f"{rows}x{cols} comic storyboard grid, exactly {total} panels total, "
        f"strict {rows}-row by {cols}-column layout, "
        f"every panel is exactly the same size and shape, "
        f"equal width equal height for all panels, "
        f"thin black border lines between panels, "
        f"no partial panels, no missing panels, no extra panels"
    )

    ref_section = (
        f"[Reference images - match appearance exactly]\n{ref_notes}"
        if ref_notes and "无已锁定" not in ref_notes
        else ""
    )

    global_constraint = (
        "maintain consistent character appearance across all panels based on reference images, "
        "same art style throughout, "
        "only include characters specified in each panel description, "
        "panels with no character description must show environment only, "
        "no text, no speech bubbles, no panel numbers, no watermarks"
    )
    if l1_summary:
        global_constraint += f", narrative context: {l1_summary[:80]}"

    panels_text = "\n".join(
        f"Panel {d['panel_num']}: {d['desc']}"
        for d in sorted(panel_descs, key=lambda x: x["panel_num"])
    )

    parts = [p for p in [header, ref_section, panels_text,
                          global_constraint, style_suffix] if p]
    return "\n\n".join(parts)


# ════════════════════════════════════════════════════════════
# PIL 切分
# ════════════════════════════════════════════════════════════

def split_grid(
    img_path: str,
    rows: int,
    cols: int,
    output_dir: str = "images",
    seg_id: int = 0,
) -> dict[int, str]:
    from PIL import Image
    import io
    import uuid as _uuid

    os.makedirs(output_dir, exist_ok=True)

    if str(img_path).startswith("http"):
        import requests
        resp = requests.get(img_path, timeout=30)
        resp.raise_for_status()
        img = Image.open(io.BytesIO(resp.content)).convert("RGB")
    else:
        img = Image.open(img_path).convert("RGB")

    w, h   = img.size
    cell_w = w // cols
    cell_h = h // rows
    ts     = int(time.time())
    uid    = _uuid.uuid4().hex[:8]

    cells = {}
    for r in range(rows):
        for c in range(cols):
            cell_num  = r * cols + c + 1
            cell_img  = img.crop((
                c * cell_w,
                r * cell_h,
                (c + 1) * cell_w,
                (r + 1) * cell_h,
            ))
            cell_path = os.path.join(output_dir, f"cell_{ts}_{seg_id}_{uid}_{cell_num}.png")
            cell_img.save(cell_path, format="PNG")
            cells[cell_num] = os.path.abspath(cell_path)

    return cells


# ════════════════════════════════════════════════════════════
# 格子编号工具
# ════════════════════════════════════════════════════════════

def get_cell_num(row: int, col: int, cols: int) -> int:
    """(row, col) 均为0-based → 返回1-based编号"""
    return row * cols + col + 1


# ════════════════════════════════════════════════════════════
# 内部工具
# ════════════════════════════════════════════════════════════

def _format_beats_fields(beats: list, shot_fields: dict, char_ref_map: dict | None = None) -> str:
    """格式化beats的骨架字段为可读文本，包含台词信息"""
    lines = []
    for i, b in enumerate(beats):
        bid       = b["beat_id"]
        fields    = shot_fields.get(bid, {})
        chars     = b.get("characters") or []
        btype     = b.get("type", "action")

        # 台词信息
        dialogue         = fields.get("dialogue", "") or b.get("line", "") or ""
        dialogue_emotion = fields.get("dialogue_emotion", "") or ""
        audio_type       = fields.get("audio_type", b.get("audio_type", "sfx"))
        energy_type      = fields.get("energy_type", "Neutral")
        motion_vector    = fields.get("motion_vector", "static")
        speaker          = b.get("speaker", "") or fields.get("speaker", "") or ""

        # panel级人物信息：用视觉代称+参考图编号，不用角色名
        if chars and char_ref_map:
            char_descs = []
            for c in chars:
                info    = char_ref_map.get(c, {})
                ref_num = info.get("ref_num")
                alias   = info.get("alias", c)
                if ref_num:
                    char_descs.append(f"参考图{ref_num}（{alias}）")
                else:
                    char_descs.append(f"{alias}（无参考图）")
            char_info = "、".join(char_descs)
        elif chars:
            char_info = "、".join(chars)
        else:
            char_info = "无人物 — pure environment, no human figures"

        # 台词行：speaker也用代称
        dialogue_line = ""
        if dialogue and audio_type in ("dialogue", "inner", "narration"):
            emotion_tag = f"（{dialogue_emotion}）" if dialogue_emotion else ""
            type_tag = {
                "dialogue":  "对白",
                "inner":     "内心",
                "narration": "旁白",
            }.get(audio_type, "")
            if speaker and char_ref_map and speaker in char_ref_map:
                info    = char_ref_map[speaker]
                ref_num = info.get("ref_num")
                alias   = info.get("alias", speaker)
                spk_label = f"参考图{ref_num}（{alias}）" if ref_num else alias
            else:
                spk_label = speaker
            if spk_label:
                dialogue_line = f"  台词[{type_tag}]：{spk_label}{emotion_tag}：「{dialogue}」"
            else:
                dialogue_line = f"  台词[{type_tag}]：{emotion_tag}「{dialogue}」"

        block = (
            f"Panel {i+1} | {bid}\n"
            f"  类型: {btype} | 音频: {audio_type}\n"
            f"  能量: {energy_type} | 动势线: {motion_vector}\n"
            f"  原文: {b.get('raw_text','')}\n"
            f"  人物: {char_info}\n"
            f"  场景: {b.get('scene_name') or b.get('scene','')}\n"
            f"  景别: {fields.get('shot_scale','MS')} | 机位: {fields.get('camera_angle','eye_level')}\n"
            f"  光线: {fields.get('lighting','soft_natural')} | 情绪: {fields.get('mood','')}\n"
            f"  画面骨架: {fields.get('subject','')}"
        )
        if dialogue_line:
            block += f"\n{dialogue_line}"

        lines.append(block)
    return "\n\n".join(lines)


def _fallback_desc(beat: dict, shot_fields: dict, style_suffix: str) -> str:
    """LLM漏掉panel时的兜底描述"""
    bid    = beat["beat_id"]
    fields = shot_fields.get(bid, {})
    parts  = [
        fields.get("shot_scale", "medium shot"),
        fields.get("camera_angle", "eye level"),
        fields.get("subject", ""),
        fields.get("lighting", "soft natural light"),
        fields.get("mood", ""),
        style_suffix,
    ]
    return ", ".join(p for p in parts if p)


def _parse_json_array(text: str) -> list:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
    if text.endswith("```"):
        text = text.rsplit("\n", 1)[0]
    text  = text.strip()
    start = text.find("[")
    end   = text.rfind("]")
    if start == -1 or end == -1:
        return []
    try:
        return json.loads(text[start:end+1])
    except Exception:
        return []