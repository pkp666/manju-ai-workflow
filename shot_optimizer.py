"""
shot_optimizer.py — Tab4 图像字段扩写

四层架构：
  Layer0: 全局预读    (1次调用，串行)
  Layer1: 叙事分析    (segment间并发)
  Layer2: 景别规划    (segment间并发)
  Layer3: 逐beat执行  (segment间并发 + segment内串行滚动窗口)

公开接口：
  run_all(beats, segments, global_state) -> dict[beat_id, fields]
  run_segment(seg_id, beats, global_state) -> dict[beat_id, fields]
  run_single_beat(beat, context) -> dict

数据结构：
  shot_fields[beat_id] = {
      # 骨架字段（Layer2锁定）
      "shot_scale":    "MCU",
      "camera_angle":  "low_angle",
      "lighting":      "candlelight",
      "mood":          "tense oppressive",
      # 执行字段（Layer3填写）
      "subject":       "...",
      "rationale":     "...",
      "reasoning":     "...",
      "step3_checks":  {...},
      # 元字段
      "status":        "done",   # pending/done/edited/skip
      "layer2_note":   "...",    # Layer2的规划备注
  }
"""

import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from llm import call_llm
from shot_optimizer_prompts import (
    LAYER0_SYSTEM, LAYER0_USER,
    LAYER1_SYSTEM, LAYER1_USER,
    LAYER2_SYSTEM, LAYER2_USER,
    LAYER3_SYSTEM, LAYER3_USER,
)


# ════════════════════════════════════════════════════════════
# 公开接口
# ════════════════════════════════════════════════════════════

def run_all(
    beats: list[dict],
    char_assets: dict,
    scene_assets: dict,
    style_suffix: str,
    story_tone: str = "",
    refined_script: str = "",
    chars_init: dict | None = None,
) -> dict[str, dict]:
    """
    一键扩写全部segment
    返回：{beat_id: fields}
    """
    ctx = _build_global_context(
        beats, char_assets, scene_assets,
        style_suffix, story_tone, refined_script,
        chars_init=chars_init,
    )

    # Layer0：全局预读
    ctx["blueprint"] = _layer0(ctx)

    # Layer1：叙事分析（并发）
    ctx["l1_results"] = _run_parallel_layer(
        _layer1, ctx["segments"], ctx
    )

    # Layer2：景别规划（并发）
    ctx["l2_results"] = _run_parallel_layer(
        _layer2, ctx["segments"], ctx
    )

    # Layer3：逐beat执行（segment间并发 + segment内串行）
    results = _run_layer3_all(ctx)

    return results


def run_segment(
    seg_id: int,
    beats: list[dict],
    char_assets: dict,
    scene_assets: dict,
    style_suffix: str,
    story_tone: str = "",
    refined_script: str = "",
    existing_shot_fields: dict = None,
    chars_init: dict | None = None,
) -> dict[str, dict]:
    """
    单独扩写一个segment
    existing_shot_fields：已有的其他segment结果，用于提取前段尾巴
    返回：{beat_id: fields}
    """
    ctx = _build_global_context(
        beats, char_assets, scene_assets,
        style_suffix, story_tone, refined_script,
        chars_init=chars_init,
    )

    # 只有目标segment的数据
    seg_beats = [b for b in beats if b.get("segment") == seg_id]
    if not seg_beats:
        return {}

    # Layer0（如果没有blueprint则重新跑）
    if "blueprint" not in ctx:
        ctx["blueprint"] = _layer0(ctx)

    # Layer1 + Layer2（只跑目标segment）
    ctx["l1_results"] = {
        seg_id: _layer1(seg_id, ctx["segments"][seg_id], ctx)
    }
    ctx["l2_results"] = {
        seg_id: _layer2(seg_id, ctx["segments"][seg_id], ctx)
    }

    # 前段尾巴（从existing_shot_fields取）
    prev_tail = _extract_prev_tail(seg_id, beats, existing_shot_fields or {})

    # Layer3：单segment串行
    results = _run_layer3_segment(seg_id, seg_beats, ctx, prev_tail)
    return results


def run_single_beat(
    beat: dict,
    shot_fields: dict,
    char_assets: dict,
    scene_assets: dict,
    style_suffix: str,
    story_tone: str = "",
    refined_script: str = "",
    chars_init: dict | None = None,
) -> dict:
    """
    单独重新扩写一个beat
    shot_fields：当前所有已有字段，用于构建上下文
    返回：单个beat的fields
    """
    seg_id    = beat.get("segment", 1)
    beat_id   = beat.get("beat_id", "")
    seg_beats = [b for b in shot_fields.values()
                 if isinstance(b, dict)]  # 同segment已完成的beat

    # 取本beat之前的所有beat作为history
    all_beats_in_seg = _get_seg_beats_from_fields(seg_id, shot_fields)
    history = []
    for b_id, fields in all_beats_in_seg.items():
        if b_id == beat_id:
            break
        history.append({**fields, "beat_id": b_id})

    # 用已有的l2字段作为骨架
    l2_bone = {
        "shot_scale":   shot_fields.get(beat_id, {}).get("shot_scale", "MS"),
        "camera_angle": shot_fields.get(beat_id, {}).get("camera_angle", "eye_level"),
        "lighting":     shot_fields.get(beat_id, {}).get("lighting", "soft_natural"),
        "mood":         shot_fields.get(beat_id, {}).get("mood", ""),
    }

    # 构建最小context
    ctx = {
        "story_tone":     story_tone,
        "refined_script": refined_script,
        "style_suffix":   style_suffix,
        "char_assets":    char_assets,
        "scene_assets":   scene_assets,
        "chars_init":     chars_init or {},
        "blueprint":      {},
        "l1_results":     {seg_id: {}},
        "l2_results":     {seg_id: {"beats": {beat_id: l2_bone}}},
    }

    return _layer3_beat(beat, history, ctx)


# ════════════════════════════════════════════════════════════
# 全局上下文构建
# ════════════════════════════════════════════════════════════

def _build_global_context(
    beats, char_assets, scene_assets,
    style_suffix, story_tone, refined_script,
    chars_init: dict | None = None,
) -> dict:
    """构建全局上下文，所有层共享"""
    segments = {}
    for b in beats:
        sid = b.get("segment", 1)
        segments.setdefault(sid, []).append(b)

    total_segs = len(segments)

    return {
        "beats":          beats,
        "segments":       segments,
        "total_segs":     total_segs,
        "char_assets":    char_assets,
        "scene_assets":   scene_assets,
        "style_suffix":   style_suffix,
        "story_tone":     story_tone,
        "refined_script": refined_script,
        "chars_init":     chars_init or {},
        # 以下由各层填充
        "blueprint":   {},
        "l1_results":  {},
        "l2_results":  {},
    }


# ════════════════════════════════════════════════════════════
# Layer0：全局预读
# ════════════════════════════════════════════════════════════

def _layer0(ctx: dict) -> dict:
    """
    全局镜头蓝图生成
    输出：{seg_id: {narrative_role, emotion_arc, scale_tendency,
                    light_lock, axis_note, key_beats,
                    rhythm, connect_prev, connect_next}}
    """
    seg_list = [
        {
            "seg_id":   sid,
            "seg_desc": beats[0].get("segment_desc", ""),
            "narrative_function": beats[0].get("narrative_function", ""),
            "beat_count": len(beats),
        }
        for sid, beats in sorted(ctx["segments"].items())
    ]

    prompt = (LAYER0_USER
        .replace("{story_tone}",     ctx["story_tone"])
        .replace("{refined_script}", ctx["refined_script"][:4000])
        .replace("{segment_list}",   json.dumps(seg_list, ensure_ascii=False, indent=2))
        .replace("{total_segs}",     str(ctx["total_segs"]))
    )

    raw    = call_llm(prompt, LAYER0_SYSTEM)
    result = _parse_json(raw)

    # 转为 {seg_id: blueprint_item} 的字典
    if isinstance(result, list):
        return {item["seg_id"]: item for item in result if "seg_id" in item}
    return {}


# ════════════════════════════════════════════════════════════
# Layer1：叙事分析
# ════════════════════════════════════════════════════════════

def _layer1(seg_id: int, seg_beats: list, ctx: dict) -> dict:
    """
    单个segment的叙事分析
    输出：{core_conflict, emotion_arc, axis, light_lock,
           key_beat_notes, scale_sequence_plan}
    """
    blueprint_item = ctx["blueprint"].get(seg_id, {})
    narrative_phase = _infer_phase(seg_id, ctx["total_segs"])

    beats_text = "\n".join(
        f"  {b['beat_id']} [{b.get('type','action')}]"
        f" 人物:{','.join(b.get('characters') or []) or '无'}"
        f" 场景:{b.get('scene_name') or b.get('scene','')}\n"
        f"  原文：{b.get('raw_text','')}"
        for b in seg_beats
    )

    prompt = (LAYER1_USER
        .replace("{story_tone}",      ctx["story_tone"])
        .replace("{refined_script}",  ctx["refined_script"][:4000])
        .replace("{seg_id}",          str(seg_id))
        .replace("{total_segs}",      str(ctx["total_segs"]))
        .replace("{narrative_phase}", narrative_phase)
        .replace("{seg_desc}",        seg_beats[0].get("segment_desc", ""))
        .replace("{blueprint_item}",  json.dumps(blueprint_item, ensure_ascii=False))
        .replace("{beats_text}",      beats_text)
    )

    raw    = call_llm(prompt, LAYER1_SYSTEM)
    return _parse_json(raw) or {}


# ════════════════════════════════════════════════════════════
# Layer2：景别规划
# ════════════════════════════════════════════════════════════

def _layer2(seg_id: int, seg_beats: list, ctx: dict) -> dict:
    """
    单个segment的景别规划
    输出：{beats: {beat_id: {scale, angle, lighting, mood, note}},
           scale_sequence, validation}
    """
    l1 = ctx["l1_results"].get(seg_id, {})

    beats_text = "\n".join(
        f"  {b['beat_id']} [{b.get('type','action')}]"
        f" 原文：{b.get('raw_text','')}"
        for b in seg_beats
        if b.get("type") != "caption"
    )

    prompt = (LAYER2_USER
        .replace("{seg_id}",         str(seg_id))
        .replace("{seg_desc}",       seg_beats[0].get("segment_desc", ""))
        .replace("{l1_result}",      json.dumps(l1, ensure_ascii=False, indent=2))
        .replace("{beats_text}",     beats_text)
        .replace("{narrative_phase}", _infer_phase(seg_id, ctx["total_segs"]))
    )

    raw    = call_llm(prompt, LAYER2_SYSTEM)
    result = _parse_json(raw) or {}

    # 标准化为 {beat_id: bone} 的字典
    beats_bone = {}
    for item in result.get("beats", []):
        bid = item.get("beat_id", "")
        if bid:
            beats_bone[bid] = {
                "shot_scale":    item.get("scale", "MS"),
                "camera_angle":  item.get("angle", "eye_level"),
                "lighting":      item.get("lighting", "soft_natural"),
                "mood":          item.get("mood", ""),
                "layer2_note":   item.get("note", ""),
            }

    return {
        "beats":          beats_bone,
        "scale_sequence": result.get("scale_sequence", ""),
        "validation":     result.get("validation", ""),
    }


# ════════════════════════════════════════════════════════════
# Layer3：逐beat执行
# ════════════════════════════════════════════════════════════

def _run_layer3_all(ctx: dict) -> dict[str, dict]:
    """
    所有segment的Layer3：segment间并发，segment内串行
    段落边界补丁：前一个segment完成后把尾巴传给下一个
    """
    results = {}  # {beat_id: fields}
    lock    = threading.Lock()

    # 按seg_id排序，用于提取前段尾巴
    seg_ids = sorted(ctx["segments"].keys())

    # 用Future追踪每个segment的完成状态
    seg_futures = {}

    def worker(seg_id):
        seg_beats = ctx["segments"][seg_id]

        # 段落边界补丁：尝试获取前一个segment的尾巴
        prev_sid  = seg_id - 1
        prev_tail = None
        if prev_sid in seg_futures:
            try:
                # 等待前一个segment完成，最多等60秒
                prev_result = seg_futures[prev_sid].result(timeout=60)
                prev_tail   = _extract_tail_from_result(
                    prev_sid, ctx["segments"].get(prev_sid, []), prev_result
                )
            except Exception:
                # 前一个超时或失败，用第一层的connect描述代替
                prev_tail = None

        seg_result = _run_layer3_segment(seg_id, seg_beats, ctx, prev_tail)

        with lock:
            results.update(seg_result)

        return seg_result

    with ThreadPoolExecutor(max_workers=len(seg_ids)) as pool:
        # 按顺序提交，但每个segment提交后不等待
        for sid in seg_ids:
            future = pool.submit(worker, sid)
            seg_futures[sid] = future

        # 等所有完成
        for future in seg_futures.values():
            try:
                future.result()
            except Exception as e:
                print(f"[shot_optimizer] layer3 segment failed: {e}")

    return results


def _run_layer3_segment(
    seg_id: int,
    seg_beats: list,
    ctx: dict,
    prev_tail: list | None = None,
) -> dict[str, dict]:
    """
    单个segment的Layer3：beat串行，滚动窗口
    prev_tail：前一个segment最后2个beat的fields，段落边界补丁
    """
    history = list(prev_tail or [])  # 滚动窗口初始状态
    results = {}

    for beat in seg_beats:
        # caption类型直接跳过
        if beat.get("type") == "caption":
            results[beat["beat_id"]] = {"status": "skip"}
            continue

        fields = _layer3_beat(beat, history, ctx)
        history.append({**fields, "beat_id": beat["beat_id"]})
        results[beat["beat_id"]] = fields

    return results


def _layer3_beat(
    beat: dict,
    history: list[dict],
    ctx: dict,
) -> dict:
    """
    单个beat的Layer3执行
    """
    beat_id = beat.get("beat_id", "")
    seg_id  = beat.get("segment", 1)

    l1     = ctx["l1_results"].get(seg_id, {})
    l2     = ctx["l2_results"].get(seg_id, {})
    bone   = l2.get("beats", {}).get(beat_id, {})

    # 资产上下文
    asset_ctx = _build_asset_context(beat, ctx["char_assets"], ctx["scene_assets"])

    # 历史摘要（滚动窗口）
    history_text = _format_history(history)

    # 前一beat的subject末尾（用于衔接提示）
    prev_subject = ""
    if history:
        prev_subject = history[-1].get("subject", "")

    # audio_type：从beat字段取，兜底按type推断
    audio_type = beat.get("audio_type", "")
    if not audio_type:
        btype = beat.get("type", "action")
        type_to_audio = {
            "dialogue":  "dialogue",
            "narration": "narration",
            "inner":     "inner",
            "action":    "sfx",
            "caption":   "sfx",
        }
        audio_type = type_to_audio.get(btype, "sfx")

    speaker = beat.get("speaker", "") or ""

    # dialogue_structure_note + dialogue_emotion
    # 优先用dialogue_structure_ref精准定位，兜底遍历匹配
    ds_list          = beat.get("dialogue_structure", [])
    ds_ref           = beat.get("dialogue_structure_ref", -1)
    ds_note          = ""
    dialogue_emotion = ""

    # 精准定位
    matched_d = None
    if ds_ref >= 0 and ds_ref < len(ds_list):
        matched_d = ds_list[ds_ref]
    else:
        # 兜底：按speaker+audio_type遍历
        for d in ds_list:
            match_speaker = (d.get("speaker", "") == speaker) if speaker else False
            match_type    = (d.get("type", "") == audio_type)
            if match_speaker or match_type:
                matched_d = d
                break

    if matched_d:
        style_note = matched_d.get("style_note", "")
        trigger    = matched_d.get("trigger", "")
        if style_note:
            emotion_part     = style_note.split("，")[0].split(",")[0].strip()
            dialogue_emotion = emotion_part
        if style_note or trigger:
            ds_note = f"触发：{trigger}  风格备注：{style_note}"

    if not ds_note:
        ds_note = "（无对应台词规划）"
    if not dialogue_emotion:
        dialogue_emotion = beat.get("mood", "") or "平静"

    # 提前提取dialogue（LLM调用前，注入prompt）
    pre_dialogue = _quick_extract_dialogue(beat)

    prompt = (LAYER3_USER
        .replace("{story_tone}",              ctx["story_tone"])
        .replace("{refined_script}",          ctx["refined_script"][:2000])
        .replace("{seg_id}",                  str(seg_id))
        .replace("{total_segs}",              str(ctx.get("total_segs", 1)))
        .replace("{narrative_phase}",         _infer_phase(seg_id, ctx.get("total_segs", 1)))
        .replace("{l1_result}",               json.dumps(l1, ensure_ascii=False))
        .replace("{beat_id}",                 beat_id)
        .replace("{beat_type}",               beat.get("type", "action"))
        .replace("{audio_type}",              audio_type)
        .replace("{speaker}",                 speaker or "无")
        .replace("{dialogue}",                pre_dialogue or "（无台词）")
        .replace("{dialogue_emotion}",        dialogue_emotion)
        .replace("{dialogue_structure_note}", ds_note)
        .replace("{raw_text}",                beat.get("raw_text", ""))
        .replace("{characters}",              ", ".join(beat.get("characters") or []) or "无")
        .replace("{scene}",                   beat.get("scene_name") or beat.get("scene", ""))
        .replace("{shot_scale}",              bone.get("shot_scale", "MS"))
        .replace("{camera_angle}",            bone.get("camera_angle", "eye_level"))
        .replace("{lighting}",                bone.get("lighting", "soft_natural"))
        .replace("{mood}",                    bone.get("mood", ""))
        .replace("{layer2_note}",             bone.get("layer2_note", ""))
        .replace("{asset_context}",           asset_ctx)
        .replace("{history_text}",            history_text)
        .replace("{prev_subject}",            prev_subject)
    )

    raw    = call_llm(prompt, LAYER3_SYSTEM)
    result = _parse_json(raw) or {}

    # dialogue：优先用LLM精简后的，兜底用快速提取
    dialogue = pre_dialogue
    if beat.get("type") in ("dialogue", "narration", "inner") and (not pre_dialogue or len(pre_dialogue) > 15):
        refined = _extract_and_refine_dialogue(beat=beat, ctx=ctx)
        if refined:
            dialogue = refined

    # duration_hint：算法计算，不需要LLM
    raw_duration = result.get("duration_hint", 0)
    if not raw_duration or raw_duration <= 0:
        dlg_len = len(dialogue) if dialogue else 0
        if audio_type == "dialogue":
            raw_duration = max(2, dlg_len // 3)
        elif audio_type == "inner":
            raw_duration = max(2, dlg_len // 4)
        elif audio_type == "narration":
            raw_duration = max(2, dlg_len // 3)
        else:  # sfx
            raw_duration = 2

    return {
        "beat_id":         beat_id,
        "shot_scale":      bone.get("shot_scale", result.get("shot_scale", "MS")),
        "camera_angle":    bone.get("camera_angle", result.get("camera_angle", "eye_level")),
        "lighting":        bone.get("lighting", result.get("lighting", "soft_natural")),
        "mood":            bone.get("mood", result.get("mood", "")),
        "subject":         result.get("subject", ""),
        "rationale":       result.get("rationale", ""),
        "reasoning":       result.get("reasoning", ""),
        "step3_checks":    result.get("step3_checks", {}),
        "layer2_note":     bone.get("layer2_note", ""),
        "audio_type":      audio_type,
        "energy_type":     result.get("energy_type", "Neutral"),
        "motion_vector":   result.get("motion_vector", "static"),
        "dialogue":        dialogue,
        "dialogue_emotion":result.get("dialogue_emotion", dialogue_emotion),
        "duration_hint":   raw_duration,
        "status":          "done",
    }


# ════════════════════════════════════════════════════════════
# 并发辅助
# ════════════════════════════════════════════════════════════

def _run_parallel_layer(fn, segments: dict, ctx: dict) -> dict:
    """
    并发执行某一层的所有segment
    fn: (seg_id, seg_beats, ctx) -> result
    """
    results = {}
    lock    = threading.Lock()

    def worker(seg_id, seg_beats):
        try:
            result = fn(seg_id, seg_beats, ctx)
            with lock:
                results[seg_id] = result
        except Exception as e:
            print(f"[shot_optimizer] layer failed seg {seg_id}: {e}")
            with lock:
                results[seg_id] = {}

    threads = [
        threading.Thread(target=worker, args=(sid, beats), daemon=True)
        for sid, beats in segments.items()
    ]
    for t in threads: t.start()
    for t in threads: t.join()
    return results


# ════════════════════════════════════════════════════════════
# 段落边界补丁工具
# ════════════════════════════════════════════════════════════

def _extract_prev_tail(
    seg_id: int,
    beats: list,
    shot_fields: dict,
    tail_count: int = 2,
) -> list[dict]:
    """
    从已有shot_fields中提取前一个segment的最后N个beat
    """
    prev_sid = seg_id - 1
    prev_beats = [b for b in beats if b.get("segment") == prev_sid]
    if not prev_beats:
        return []

    tail_beats = prev_beats[-tail_count:]
    tail = []
    for b in tail_beats:
        bid = b.get("beat_id", "")
        if bid in shot_fields and shot_fields[bid].get("status") == "done":
            tail.append({**shot_fields[bid], "beat_id": bid})
    return tail


def _extract_tail_from_result(
    seg_id: int,
    seg_beats: list,
    seg_result: dict,
    tail_count: int = 2,
) -> list[dict]:
    """
    从刚完成的segment结果里提取最后N个beat
    """
    done_beats = [
        {**seg_result[b["beat_id"]], "beat_id": b["beat_id"]}
        for b in seg_beats
        if b.get("beat_id") in seg_result
        and seg_result[b["beat_id"]].get("status") == "done"
    ]
    return done_beats[-tail_count:]


# ════════════════════════════════════════════════════════════
# 工具函数
# ════════════════════════════════════════════════════════════

def _infer_phase(seg_id: int, total_segs: int) -> str:
    if total_segs <= 1:
        return "建立(establish)"
    ratio = (seg_id - 1) / max(total_segs - 1, 1)
    if ratio <= 0.15: return "建立(establish) — 交代背景，节奏舒缓"
    if ratio <= 0.40: return "升温(escalate) — 矛盾浮现，情绪积累"
    if ratio <= 0.65: return "高潮(climax) — 冲突爆发，情绪最强"
    if ratio <= 0.85: return "转折(turn) — 局势反转，节奏突变"
    return "余韵(aftermath) — 情绪收尾，节奏放缓"


def _build_asset_context(beat: dict, char_assets: dict, scene_assets: dict) -> str:
    lines = []
    for c in beat.get("characters") or []:
        asset = char_assets.get(c, {})
        has_img = bool(asset.get("base", {}).get("locked_img"))
        lines.append(f"  {c}：{'已锁定立绘（生图时传参考图）' if has_img else '无锁定立绘'}")

    scene = beat.get("scene_name") or beat.get("scene", "")
    if scene and scene in scene_assets:
        s = scene_assets[scene]
        has_img = bool(s.get("locked_img"))
        lines.append(f"  场景{scene}：{'已锁定场景图' if has_img else '无场景图'}")

    return "\n".join(lines) if lines else "  暂无已锁定资产"


def _format_history(history: list[dict]) -> str:
    if not history:
        return "（无，这是本segment第一个beat）"
    lines = []
    for h in history:
        bid = h.get("beat_id", "")
        lines.append(
            f"  {bid}: {h.get('shot_scale','')} + {h.get('camera_angle','')} "
            f"+ {h.get('lighting','')} + {h.get('mood','')}\n"
            f"    subject: {h.get('subject','')[:100]}..."
        )
    return "\n".join(lines)


def _get_seg_beats_from_fields(seg_id: int, shot_fields: dict) -> dict:
    """从shot_fields里按顺序取某个segment的beat"""
    return {
        bid: fields for bid, fields in shot_fields.items()
        if isinstance(fields, dict) and fields.get("segment") == seg_id
    }


def _quick_extract_dialogue(beat: dict) -> str:
    """
    提取beat的台词内容。

    优先级：
    1. beat.dialogues字段（Tab1台词提取后的结构化数据）
    2. 正则从raw_text提取（兜底）
    3. type=dialogue/narration/inner且speaker存在 → 优先读line字段

    返回台词内容字符串，无台词返回空字符串。
    """
    import re

    # 优先：读dialogues结构化字段
    dialogues = beat.get("dialogues", [])
    if dialogues:
        # 取第一条台词的line字段
        first = dialogues[0]
        if isinstance(first, dict) and first.get("line"):
            return first["line"]

    # 兜底1：正则从raw_text提取
    raw_text = beat.get("raw_text", "")
    if raw_text:
        pattern = r'[一-龥A-Za-z]{1,6}(?:（内心）)?："([^"]{1,80})"'
        matches = re.findall(pattern, raw_text)
        if matches:
            return matches[0]

    # 兜底2：type=line且有speaker，取raw_text前30字
    if beat.get("type") in ("dialogue", "narration", "inner") and beat.get("speaker"):
        return raw_text[:30]

    return ""


def _extract_and_refine_dialogue(beat: dict, ctx: dict) -> str:
    """
    层面3：从beat的raw_text里提取台词，按人物speech_style精简到≤15字。

    步骤：
      1. 从raw_text里解析标准格式台词（角色名："台词"）
      2. 有speech_style的角色 → LLM按风格精简
      3. 无speech_style → 直接精简长度
      4. 存入shot_fields供Tab7使用
    """
    import re

    raw_text   = beat.get("raw_text", "")
    speaker    = beat.get("speaker", "")
    chars_init = ctx.get("chars_init", {})

    # Step1：从raw_text解析台词
    # 匹配：角色名："台词" 或 角色名（内心）："台词"
    pattern = r'(\S{1,6})(?:（内心）)?："([^"]{1,60})"'
    matches = re.findall(pattern, raw_text)

    if not matches:
        # 兜底：整段raw_text作为台词（type=line但格式不标准）
        if speaker and len(raw_text) <= 30:
            return f"【{speaker}】{raw_text}"
        return ""

    # 取第一句台词（最重要的）
    spk, dlg = matches[0]
    is_inner  = "（内心）" in raw_text and f'{spk}（内心）' in raw_text

    # Step2：判断是否需要按风格精简
    char_info  = chars_init.get(spk, {})
    speech_style = char_info.get("speech_style", "").strip()
    SKIP = {"待补充", "（台词样本不足，待补充）", ""}

    # 台词已经≤15字且无风格要求 → 直接返回
    if len(dlg) <= 15 and speech_style in SKIP:
        prefix = "（内心）" if is_inner else ""
        return f"【{spk}{prefix}】{dlg}"

    # 台词≤15字且有风格 → 只做风格微调，不调用LLM
    if len(dlg) <= 15 and speech_style not in SKIP:
        prefix = "（内心）" if is_inner else ""
        return f"【{spk}{prefix}】{dlg}"

    # 台词>15字 → 调用LLM精简
    style_hint = f"说话风格：{speech_style}" if speech_style not in SKIP else "无特殊风格要求"
    inner_hint = "内心独白，语气内敛" if is_inner else "对白，语气外放"

    _REFINE_SYSTEM = """你是漫剧台词编辑，把过长的台词精简为漫剧语言。
规则：
  保留核心语义（这句话要表达什么）
  按角色说话风格改写
  控制在15字以内
  不加引号，直接输出台词文字"""

    _REFINE_USER = f"""角色：{spk}
{style_hint}
台词类型：{inner_hint}
原台词（{len(dlg)}字）：{dlg}

请精简为15字以内的漫剧台词，直接输出台词文字："""

    try:
        refined = call_llm(_REFINE_USER, _REFINE_SYSTEM).strip()
        # 清理可能的引号
        refined = refined.strip('"').strip('"').strip('"').strip()
        if refined:
            prefix = "（内心）" if is_inner else ""
            return f"【{spk}{prefix}】{refined}"
    except Exception as e:
        print(f"[shot_optimizer] dialogue refine failed: {e}")

    # LLM失败兜底：截断
    prefix = "（内心）" if is_inner else ""
    return f"【{spk}{prefix}】{dlg[:15]}"


def _parse_json(text: str) -> dict | list | None:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
    if text.endswith("```"):
        text = text.rsplit("\n", 1)[0]
    text = text.strip()

    # 尝试找JSON对象或数组
    for start_char, end_char in [('{', '}'), ('[', ']')]:
        s = text.find(start_char)
        e = text.rfind(end_char)
        if s != -1 and e != -1:
            try:
                return json.loads(text[s:e+1])
            except Exception:
                continue
    return None


# ════════════════════════════════════════════════════════════
# 兼容旧接口（供app.py的Tab4 UI调用）
# ════════════════════════════════════════════════════════════

def optimize_segment(seg_beats, total_segments, char_assets, scene_assets, style_suffix):
    """旧接口兼容，单segment扩写，无全局上下文"""
    if not seg_beats:
        return []
    seg_id = seg_beats[0].get("segment", 1)
    ctx = {
        "beats":          seg_beats,
        "segments":       {seg_id: seg_beats},
        "total_segs":     total_segments,
        "char_assets":    char_assets,
        "scene_assets":   scene_assets,
        "style_suffix":   style_suffix,
        "story_tone":     "",
        "refined_script": "",
        "blueprint":      {},
        "l1_results":     {seg_id: _layer1(seg_id, seg_beats, {
            "story_tone": "", "refined_script": "",
            "total_segs": total_segments, "blueprint": {},
            "segments": {seg_id: seg_beats},
        })},
        "l2_results":     {},
    }
    ctx["l2_results"][seg_id] = _layer2(seg_id, seg_beats, ctx)
    result = _run_layer3_segment(seg_id, seg_beats, ctx)
    return list(result.values())


def optimize_all_segments(beats, char_assets=None, scene_assets=None, style_suffix=""):
    """旧接口兼容，一键扩写全部"""
    results = run_all(
        beats,
        char_assets  or {},
        scene_assets or {},
        style_suffix,
    )
    return results


# ── 以下函数保持原有签名不变（供Tab5用）──────────────────────

def infer_narrative_phase(segment_id: int, total_segments: int) -> str:
    return _infer_phase(segment_id, total_segments)


def build_asset_context(segment_beats, char_assets, scene_assets) -> str:
    lines = []
    for b in segment_beats:
        lines.append(_build_asset_context(b, char_assets, scene_assets))
    return "\n".join(lines)


def bind_assets(beat: dict, char_assets: dict, scene_assets: dict):
    ref_chars = []
    for c in beat.get("characters") or []:
        char = char_assets.get(c, {})
        pm   = beat.get("period_map") or {}
        p    = pm.get(c, "")
        img  = (char.get("periods", {}).get(p, {}).get("locked_img")
                if p else char.get("base", {}).get("locked_img"))
        if img:
            ref_chars.append(img)

    ref_scene = None
    for key in [beat.get("scene_name"), beat.get("scene")]:
        if key and key in scene_assets:
            s = scene_assets[key]
            img = s.get("locked_img")
            if img:
                ref_scene = img
                break

    return ref_chars, ref_scene


def missing_assets(beat: dict, char_assets: dict, scene_assets: dict) -> list[str]:
    missing = []
    for c in beat.get("characters") or []:
        char = char_assets.get(c, {})
        img  = char.get("base", {}).get("locked_img")
        if not img:
            missing.append(c)
    return missing


def build_prompt_en(fields: dict, style_suffix: str) -> str:
    parts = [
        fields.get("shot_scale", ""),
        fields.get("camera_angle", ""),
        fields.get("subject", ""),
        fields.get("lighting", ""),
        fields.get("mood", ""),
        style_suffix,
    ]
    return ", ".join(p for p in parts if p)


# generate_shot_prompts_3 / optimize_shot_prompts_3
# 保持原有签名，供Tab5单帧遗传用
def generate_shot_prompts_3(beat, fields, char_assets, scene_assets,
                             style_suffix, segment_desc="",
                             narrative_phase="", total_segments=1):
    from shot_optimizer_prompts import LAYER3_SYSTEM, SHOT_GACHA_GEN_USER
    # 复用layer3_beat的逻辑，输出3个变体
    ctx = {
        "story_tone": "", "refined_script": "",
        "total_segs": total_segments,
        "char_assets": char_assets, "scene_assets": scene_assets,
        "style_suffix": style_suffix,
        "blueprint": {},
        "l1_results": {beat.get("segment", 1): {}},
        "l2_results": {beat.get("segment", 1): {"beats": {
            beat.get("beat_id", ""): {
                "shot_scale":   fields.get("shot_scale", "MS"),
                "camera_angle": fields.get("camera_angle", "eye_level"),
                "lighting":     fields.get("lighting", "soft_natural"),
                "mood":         fields.get("mood", ""),
            }
        }}},
    }
    result = _layer3_beat(beat, [], ctx)
    p = result.get("subject", "")
    return [p, p, p], [result.get("rationale", "")] * 3


def optimize_shot_prompts_3(original_prompt, beat, mood="", user_note=""):
    from shot_optimizer_prompts import SHOT_GACHA_OPTIMIZE_SYSTEM, SHOT_GACHA_OPTIMIZE_USER
    prompt = (SHOT_GACHA_OPTIMIZE_USER
        .replace("{original_prompt}", original_prompt)
        .replace("{raw_text}",        beat.get("raw_text", ""))
        .replace("{segment_desc}",    beat.get("segment_desc", ""))
        .replace("{beat_type}",       beat.get("type", "action"))
        .replace("{characters}",      ", ".join(beat.get("characters") or []))
        .replace("{mood}",            mood or "")
        .replace("{user_note}",       user_note or "无")
    )
    raw    = call_llm(prompt, SHOT_GACHA_OPTIMIZE_SYSTEM)
    parsed = _parse_json(raw) or []
    prompts    = ["", "", ""]
    rationales = ["", "", ""]
    for i, item in enumerate(parsed[:3]):
        if isinstance(item, dict):
            prompts[i]    = item.get("prompt_en", "")
            rationales[i] = item.get("rationale", "")
    return prompts, rationales