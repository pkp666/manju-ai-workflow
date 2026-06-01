"""
video_prompter.py — 视频提示词生成核心逻辑

架构：
  Layer0：全局蒸馏（1次串行）
  Layer1：segment规划（有序串行）
  Layer2：segment间并发 + segment内串行
    每beat对：两专家并发(AB+C) → 审核 → 精炼 → 自检 → [修正]
    台词由Python层提取，不靠LLM

video_fields[beat_id] = {
    video_prompt   精炼+自检通过的最终版（用于生视频）
    full_prompt    审核专家完整版（调试用）
    dialogue       台词文本（Python层提取）
    motion         运镜指令
    duration_hint  时长建议
    check_result   自检结果
    draft_ab       专家AB草稿
    draft_c        专家C草稿
    from_beat_id   首帧beat_id
    to_beat_id     尾帧beat_id
    pair_idx       beat对编号（segment内）
}
"""

import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from llm import call_llm
from shot_optimizer import infer_narrative_phase
from video_prompter_prompts import (
    LAYER0_SYSTEM, LAYER0_USER,
    LAYER1_SYSTEM, LAYER1_USER,
    EXPERT_AB_SYSTEM, EXPERT_AB_USER,
    EXPERT_C_SYSTEM, EXPERT_C_USER,
    REVIEWER_SYSTEM, REVIEWER_USER,
    REFINER_SYSTEM, REFINER_USER,
    CHECKER_SYSTEM, CHECKER_USER,
    FIXER_SYSTEM, FIXER_USER,
)


# ════════════════════════════════════════════════════════════
# 公开接口
# ════════════════════════════════════════════════════════════

def run_all(
    beats:            list[dict],
    shot_fields:      dict,
    panel_descs_map:  dict,
    char_assets:      dict,
    scene_assets:     dict,
    style_suffix:     str,
    story_tone:       str = "",
    refined_script:   str = "",
) -> dict[str, dict]:
    """主入口，返回 {beat_id: video_fields}"""

    segments   = _group_segments(beats)
    total_segs = len(segments)

    # Layer0
    director_manual = _layer0(
        beats, shot_fields, char_assets, scene_assets,
        style_suffix, story_tone, refined_script,
    )

    # Layer1（有序串行）
    segment_scripts = _layer1_all(
        segments, beats, shot_fields,
        panel_descs_map, director_manual,
        story_tone, total_segs,
    )

    all_scripts_str = _format_all_scripts(segment_scripts)

    # Layer2（segment间并发，segment内串行）
    results = _layer2_all(
        beats, segments, shot_fields,
        panel_descs_map, segment_scripts,
        all_scripts_str, director_manual,
    )

    return results


def run_segment(
    seg_id:           int,
    beats:            list[dict],
    shot_fields:      dict,
    panel_descs_map:  dict,
    char_assets:      dict,
    scene_assets:     dict,
    style_suffix:     str,
    story_tone:       str = "",
    refined_script:   str = "",
    existing_results: dict = None,
) -> dict[str, dict]:
    """单独重新生成一个segment"""

    segments   = _group_segments(beats)
    total_segs = len(segments)

    director_manual = _layer0(
        beats, shot_fields, char_assets, scene_assets,
        style_suffix, story_tone, refined_script,
    )

    seg_beats   = segments.get(seg_id, [])
    prev_script = ""
    if seg_id - 1 in segments:
        prev_script = (existing_results or {}).get(
            f"__seg_script_{seg_id-1}", "")

    seg_script = _layer1_segment(
        seg_id, seg_beats, beats, shot_fields,
        panel_descs_map, director_manual,
        prev_script, story_tone, total_segs,
    )

    all_scripts_str = _format_all_scripts({seg_id: seg_script})
    prior_memory    = _extract_boundary_memory(
        seg_id, beats, existing_results or {})

    results = _layer2_segment(
        seg_id, seg_beats, shot_fields,
        panel_descs_map, {seg_id: seg_script},
        all_scripts_str, director_manual, prior_memory,
    )
    return results


# ════════════════════════════════════════════════════════════
# Layer0
# ════════════════════════════════════════════════════════════

def _layer0(beats, shot_fields, char_assets, scene_assets,
            style_suffix, story_tone, refined_script) -> str:

    chars  = list(char_assets.keys())
    scenes = list(scene_assets.keys())

    fields_lines = []
    for bid, f in shot_fields.items():
        if isinstance(f, dict) and f.get("status") in ("done", "edited"):
            fields_lines.append(
                f"{bid}: lighting={f.get('lighting','')} mood={f.get('mood','')}")
    fields_summary = "\n".join(fields_lines[:50])

    prompt = (LAYER0_USER
        .replace("{story_tone}",     story_tone)
        .replace("{refined_script}", refined_script[:6000])
        .replace("{style_suffix}",   style_suffix)
        .replace("{chars}",          json.dumps(chars, ensure_ascii=False))
        .replace("{scenes}",         json.dumps(scenes, ensure_ascii=False))
        .replace("{fields_summary}", fields_summary)
    )

    raw    = call_llm(prompt, LAYER0_SYSTEM)
    parsed = _parse_json(raw)
    return json.dumps(parsed, ensure_ascii=False, indent=2) if parsed else raw


# ════════════════════════════════════════════════════════════
# Layer1
# ════════════════════════════════════════════════════════════

def _layer1_all(segments, beats, shot_fields, panel_descs_map,
                director_manual, story_tone, total_segs) -> dict[int, str]:
    scripts = {}
    for sid in sorted(segments.keys()):
        prev = scripts.get(sid - 1, "（无前一segment）")
        scripts[sid] = _layer1_segment(
            sid, segments[sid], beats, shot_fields,
            panel_descs_map, director_manual,
            prev, story_tone, total_segs,
        )
    return scripts


def _layer1_segment(seg_id, seg_beats, all_beats, shot_fields,
                    panel_descs_map, director_manual,
                    prev_script, story_tone, total_segs) -> str:

    seg_desc  = seg_beats[0].get("segment_desc", "") if seg_beats else ""
    phase     = infer_narrative_phase(seg_id, total_segs)
    beats_info = _format_beats_info(seg_beats, shot_fields)
    fields_info = _format_fields_info(seg_beats, shot_fields)
    panel_info  = "\n".join(
        f"Panel {d['panel_num']} ({d['beat_id']}): {d['desc']}"
        for d in panel_descs_map.get(seg_id, [])
    )

    prompt = (LAYER1_USER
        .replace("{director_manual}",     director_manual)
        .replace("{prev_segment_script}", prev_script)
        .replace("{seg_id}",              str(seg_id))
        .replace("{total_segs}",          str(total_segs))
        .replace("{seg_desc}",            seg_desc)
        .replace("{narrative_phase}",     phase)
        .replace("{beats_info}",          beats_info)
        .replace("{fields_info}",         fields_info)
        .replace("{panel_descs_info}",    panel_info)
    )

    raw    = call_llm(prompt, LAYER1_SYSTEM)
    parsed = _parse_json(raw)
    return json.dumps(parsed, ensure_ascii=False, indent=2) if parsed else raw


# ════════════════════════════════════════════════════════════
# Layer2：segment间并发 + segment内串行
# ════════════════════════════════════════════════════════════

def _layer2_all(beats, segments, shot_fields, panel_descs_map,
                segment_scripts, all_scripts_str, director_manual) -> dict[str, dict]:
    """
    segment间并发：所有segment同时启动
    但每个segment需要等前一个segment的最后2对完成（边界记忆补丁）
    segment内严格串行
    """
    results  = {}
    lock     = threading.Lock()
    # {seg_id: 最后2对的完整prompt列表}
    boundary = {}

    def _run_seg(sid):
        seg_beats = segments[sid]

        # 等待前一个segment的边界记忆
        prev_sid = sid - 1
        prior_memory = []
        if prev_sid in segments:
            # 轮询等待边界记忆
            import time
            timeout = 600
            start   = time.time()
            while True:
                with lock:
                    if prev_sid in boundary:
                        prior_memory = boundary[prev_sid]
                        break
                if time.time() - start > timeout:
                    print(f"[video_prompter] seg{sid} 等待边界记忆超时")
                    break
                time.sleep(2)

        seg_results = _layer2_segment(
            sid, seg_beats, shot_fields,
            panel_descs_map, segment_scripts,
            all_scripts_str, director_manual,
            prior_memory,
        )

        # 提取本segment最后2对作为边界记忆
        valid_beats = [b for b in seg_beats if b.get("type") != "caption"]
        last_pairs  = []
        for i in range(max(0, len(valid_beats) - 3), len(valid_beats) - 1):
            bid_a = valid_beats[i]["beat_id"]
            if bid_a in seg_results:
                last_pairs.append({
                    "video_prompt": seg_results[bid_a].get("video_prompt", ""),
                    "from_beat_id": bid_a,
                })
        boundary_mem = last_pairs[-2:]

        with lock:
            results.update(seg_results)
            boundary[sid] = boundary_mem

    with ThreadPoolExecutor(max_workers=len(segments)) as pool:
        futures = {pool.submit(_run_seg, sid): sid
                   for sid in sorted(segments.keys())}
        for f in as_completed(futures):
            try:
                f.result()
            except Exception as e:
                print(f"[video_prompter] seg failed: {e}")

    return results


def _layer2_segment(seg_id, seg_beats, shot_fields, panel_descs_map,
                    segment_scripts, all_scripts_str, director_manual,
                    prior_memory) -> dict[str, dict]:
    """segment内严格串行"""

    valid_beats = [b for b in seg_beats if b.get("type") != "caption"]
    pairs = [
        {"from_beat": valid_beats[i], "to_beat": valid_beats[i + 1], "seg_id": seg_id}
        for i in range(len(valid_beats) - 1)
    ]

    results      = {}
    prompt_memory = list(prior_memory)  # 从边界记忆开始

    for pair_idx, pair in enumerate(pairs):
        fields = _layer2_beat_pair(
            pair, pair_idx,
            shot_fields, panel_descs_map,
            director_manual, all_scripts_str,
            prompt_memory,
        )
        from_bid = pair["from_beat"]["beat_id"]
        to_bid   = pair["to_beat"]["beat_id"]

        results[from_bid] = {**fields, "pair_role": "start"}
        if to_bid not in results:
            results[to_bid] = {**fields, "pair_role": "end"}

        # 更新记忆（不蒸馏，用完整prompt）
        prompt_memory.append({
            "from_beat_id": from_bid,
            "to_beat_id":   to_bid,
            "video_prompt": fields.get("video_prompt", ""),
        })

    return results


def _layer2_beat_pair(pair, pair_idx, shot_fields, panel_descs_map,
                      director_manual, all_scripts_str, prompt_memory) -> dict:
    """单beat对：两专家并发 → 审核 → 精炼 → 自检 → [修正]"""

    from_beat = pair["from_beat"]
    to_beat   = pair["to_beat"]

    # Python层提取台词（不靠LLM）
    from_dialogue = _extract_dialogue(from_beat)
    to_dialogue   = _extract_dialogue(to_beat)
    dialogue      = "\n".join(filter(None, [from_dialogue, to_dialogue]))

    # 构建共享上下文
    ctx = _build_ctx(
        from_beat, to_beat,
        shot_fields, panel_descs_map,
        director_manual, all_scripts_str,
        prompt_memory,
    )

    # ── 两专家并发 ──────────────────────────────────────────
    expert_results = {}
    lock = threading.Lock()

    def _run(name, system, user_tpl):
        prompt = user_tpl.format(**ctx)
        raw    = call_llm(prompt, system)
        with lock:
            expert_results[name] = _parse_json(raw) or {"raw": raw}

    ts = [
        threading.Thread(target=_run, args=("AB", EXPERT_AB_SYSTEM, EXPERT_AB_USER), daemon=True),
        threading.Thread(target=_run, args=("C",  EXPERT_C_SYSTEM,  EXPERT_C_USER),  daemon=True),
    ]
    for t in ts: t.start()
    for t in ts: t.join()

    # ── 审核 ────────────────────────────────────────────────
    reviewer_prompt = REVIEWER_USER.format(
        director_manual  = director_manual,
        expert_ab_output = json.dumps(expert_results.get("AB", {}), ensure_ascii=False, indent=2),
        expert_c_output  = json.dumps(expert_results.get("C",  {}), ensure_ascii=False, indent=2),
        from_beat_id     = from_beat["beat_id"],
        from_type        = from_beat.get("type", ""),
        from_subtype     = from_beat.get("subtype", ""),   # ← 新增
        from_raw_text    = from_beat.get("raw_text", ""),
        from_chars       = ", ".join(from_beat.get("characters") or []),
        from_dialogue    = from_dialogue or "无",
        to_beat_id       = to_beat["beat_id"],
        to_type          = to_beat.get("type", ""),
        to_subtype       = to_beat.get("subtype", ""),     # ← 新增
        to_raw_text      = to_beat.get("raw_text", ""),
        to_chars         = ", ".join(to_beat.get("characters") or []),
        to_dialogue      = to_dialogue or "无",
    )
    full_prompt = call_llm(reviewer_prompt, REVIEWER_SYSTEM).strip()

    # ── 精炼 ────────────────────────────────────────────────
    refiner_prompt = REFINER_USER.format(
        full_prompt = full_prompt,
        dialogue    = dialogue or "无",
    )
    refined = call_llm(refiner_prompt, REFINER_SYSTEM).strip()

    # Python层强制保证台词存在
    if dialogue and "台词" not in refined:
        refined = refined.rstrip() + f"\n台词：{dialogue}"

    # ── 自检 ────────────────────────────────────────────────
    char_count  = len(set(
        (from_beat.get("characters") or []) +
        (to_beat.get("characters") or [])
    ))
    checker_prompt = CHECKER_USER.format(
        refined_prompt = refined,
        char_count     = char_count,
        has_dialogue   = "是" if dialogue else "否",
        dialogue       = dialogue or "无",
    )
    check_raw    = call_llm(checker_prompt, CHECKER_SYSTEM)
    check_result = _parse_json(check_raw) or {"passed": True, "fixes_needed": []}

    # ── 修正（自检不通过时）────────────────────────────────
    final_prompt = refined
    if not check_result.get("passed", True) and check_result.get("fixes_needed"):
        fixer_prompt = FIXER_USER.format(
            refined_prompt = refined,
            fixes_needed   = "\n".join(check_result["fixes_needed"]),
            dialogue       = dialogue or "无",
        )
        fixed = call_llm(fixer_prompt, FIXER_SYSTEM).strip()
        if fixed:
            # Python层再次保证台词
            if dialogue and "台词" not in fixed:
                fixed = fixed.rstrip() + f"\n台词：{dialogue}"
            final_prompt = fixed

    # 提取motion和duration（从final_prompt里解析或从专家AB草稿取）
    ab = expert_results.get("AB", {})
    motion        = ab.get("lens_motion", "")
    duration_hint = "3-4s"

    # 从shot_fields取audio_type/dialogue_emotion/duration_hint
    from_sf      = shot_fields.get(from_beat["beat_id"], {})
    to_sf        = shot_fields.get(to_beat["beat_id"], {})
    audio_type   = from_sf.get("audio_type", from_beat.get("audio_type", "sfx"))
    dlg_emotion  = from_sf.get("dialogue_emotion", "")
    # 时长：取首帧+尾帧duration_hint之和，兜底用3s
    from_dur     = from_sf.get("duration_hint", 0) or 0
    to_dur       = to_sf.get("duration_hint", 0)   or 0
    total_dur    = (from_dur + to_dur) or 3

    return {
        "video_prompt":      final_prompt,
        "full_prompt":       full_prompt,
        "dialogue":          dialogue,
        "motion":            motion,
        "duration_hint":     f"{total_dur}s",
        "audio_type":        audio_type,
        "dialogue_emotion":  dlg_emotion,
        "check_result":      check_result,
        "draft_ab":          json.dumps(expert_results.get("AB", {}), ensure_ascii=False),
        "draft_c":           json.dumps(expert_results.get("C",  {}), ensure_ascii=False),
        "from_beat_id":      from_beat["beat_id"],
        "to_beat_id":        to_beat["beat_id"],
        "pair_idx":          pair_idx,
    }


# ════════════════════════════════════════════════════════════
# 共享上下文构建
# ════════════════════════════════════════════════════════════

def _build_ctx(from_beat, to_beat, shot_fields, panel_descs_map,
               director_manual, all_scripts_str, prompt_memory) -> dict:

    seg_id = from_beat.get("segment", 1)

    def _panel(beat_id):
        for d in panel_descs_map.get(seg_id, []):
            if d["beat_id"] == beat_id:
                return d["desc"]
        return ""

    def _fields(beat_id):
        f = shot_fields.get(beat_id, {})
        return (f"scale={f.get('shot_scale','')} angle={f.get('camera_angle','')} "
                f"lighting={f.get('lighting','')} mood={f.get('mood','')} "
                f"audio_type={f.get('audio_type','')} "
                f"energy={f.get('energy_type','')} motion_vec={f.get('motion_vector','')} "
                f"dialogue_emotion={f.get('dialogue_emotion','')} "
                f"duration={f.get('duration_hint','')}s "
                f"subject={f.get('subject','')[:80]}")

    # 记忆层：前1-2对完整，前3-6对提取key state
    mem = list(reversed(prompt_memory))
    prev_1 = mem[0]["video_prompt"] if len(mem) > 0 else "（无）"
    prev_2 = mem[1]["video_prompt"] if len(mem) > 1 else "（无）"
    summaries = []
    for m in mem[2:6]:
        vp = m.get("video_prompt", "")
        # 取前100词作为简略摘要
        summaries.append(f"pair({m.get('from_beat_id','')})→({m.get('to_beat_id','')}): {vp[:100]}...")
    prev_summaries = "\n".join(summaries) or "（无）"

    next_plan = f"下一beat位于segment {seg_id}"

    def _get(beat_id, key, default=""):
        return shot_fields.get(beat_id, {}).get(key, default)

    return dict(
        director_manual     = director_manual,
        all_segment_scripts = all_scripts_str,
        prev_1_prompt       = prev_1,
        prev_2_prompt       = prev_2,
        prev_summaries      = prev_summaries,
        from_beat_id        = from_beat["beat_id"],
        from_panel_desc     = _panel(from_beat["beat_id"]),
        from_fields         = _fields(from_beat["beat_id"]),
        from_raw_text       = from_beat.get("raw_text", ""),
        from_type           = from_beat.get("type", ""),
        from_subtype        = from_beat.get("subtype", ""),   # ← 新增
        from_chars          = ", ".join(from_beat.get("characters") or []),
        from_audio_type     = _get(from_beat["beat_id"], "audio_type",
                                   from_beat.get("audio_type", "sfx")),
        from_dialogue_emotion = _get(from_beat["beat_id"], "dialogue_emotion", ""),
        from_duration       = _get(from_beat["beat_id"], "duration_hint", 3),
        from_energy         = _get(from_beat["beat_id"], "energy_type", "Neutral"),
        to_beat_id          = to_beat["beat_id"],
        to_panel_desc       = _panel(to_beat["beat_id"]),
        to_fields           = _fields(to_beat["beat_id"]),
        to_raw_text         = to_beat.get("raw_text", ""),
        to_type             = to_beat.get("type", ""),
        to_subtype          = to_beat.get("subtype", ""),     # ← 新增
        to_chars            = ", ".join(to_beat.get("characters") or []),
        to_audio_type       = _get(to_beat["beat_id"], "audio_type",
                                   to_beat.get("audio_type", "sfx")),
        to_dialogue_emotion = _get(to_beat["beat_id"], "dialogue_emotion", ""),
        to_duration         = _get(to_beat["beat_id"], "duration_hint", 3),
        to_energy           = _get(to_beat["beat_id"], "energy_type", "Neutral"),
        next_beat_plan      = next_plan,
    )


# ════════════════════════════════════════════════════════════
# 台词提取（Python层，不靠LLM）
# ════════════════════════════════════════════════════════════

def _extract_dialogue(beat: dict) -> str:
    """
    从beat提取台词，优先用Tab4精简后的dialogue字段。
    优先级：
      1. shot_fields[beat_id]["dialogue"]（Tab4精简后）
      2. raw_text里的标准格式台词（正则解析）
      3. type=dialogue/narration/inner且有speaker → line字段兜底raw_text
    """
    import re
    import streamlit as st

    beat_id     = beat.get("beat_id", "")
    shot_fields = st.session_state.get("shot_fields", {})

    # 优先1：Tab4精简后的dialogue
    if beat_id and beat_id in shot_fields:
        d = shot_fields[beat_id].get("dialogue", "")
        if d:
            return d

    # 优先2：raw_text里的标准格式台词
    raw_text = beat.get("raw_text", "")
    if raw_text:
        pattern = r'(\S{1,6})(?:（内心）)?："([^"]{1,60})"'
        matches = re.findall(pattern, raw_text)
        if matches:
            spk, dlg = matches[0]
            is_inner = f'{spk}（内心）' in raw_text
            prefix   = "（内心）" if is_inner else ""
            return f"【{spk}{prefix}】{dlg}"

    # 优先3：兜底
    speaker = beat.get("speaker", "")
    btype   = beat.get("type", "")
    if btype in ("dialogue", "narration", "inner") and speaker and raw_text:
        return f"【{speaker}】{raw_text[:30]}"

    return ""


# ════════════════════════════════════════════════════════════
# 工具函数
# ════════════════════════════════════════════════════════════

def _group_segments(beats: list) -> dict[int, list]:
    segs = {}
    for b in beats:
        segs.setdefault(b.get("segment", 1), []).append(b)
    return segs


def _format_all_scripts(scripts: dict) -> str:
    return "\n\n".join(
        f"=== Segment {sid} ===\n{s}"
        for sid, s in sorted(scripts.items())
    )


def _extract_boundary_memory(seg_id, beats, existing_results) -> list[dict]:
    """从已有结果里提取前一segment最后2对作为边界记忆"""
    prev_beats = [
        b for b in beats
        if b.get("segment", 1) < seg_id
        and b.get("type") != "caption"
    ]
    mem = []
    for b in reversed(prev_beats):
        bid = b["beat_id"]
        if bid in existing_results:
            r = existing_results[bid]
            mem.append({
                "from_beat_id": r.get("from_beat_id", bid),
                "to_beat_id":   r.get("to_beat_id", ""),
                "video_prompt": r.get("video_prompt", ""),
            })
        if len(mem) >= 2:
            break
    return list(reversed(mem))


def _format_beats_info(beats, shot_fields) -> str:
    lines = []
    for b in beats:
        bid = b["beat_id"]
        lines.append(
            f"{bid} [{b.get('type','')}] "
            f"角色:{','.join(b.get('characters') or []) or '无'} "
            f"场景:{b.get('scene_name') or b.get('scene','')} "
            f"speaker:{b.get('speaker','无')}\n"
            f"  原文：{b.get('raw_text','')}"
        )
    return "\n".join(lines)


def _format_fields_info(beats, shot_fields) -> str:
    lines = []
    for b in beats:
        bid = b["beat_id"]
        f   = shot_fields.get(bid, {})
        if f.get("status") in ("done", "edited"):
            lines.append(
                f"{bid}: {f.get('shot_scale','')} {f.get('camera_angle','')} "
                f"{f.get('lighting','')} {f.get('mood','')} | "
                f"{f.get('subject','')[:60]}"
            )
    return "\n".join(lines)


def _parse_json(text: str):
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
    if text.endswith("```"):
        text = text.rsplit("\n", 1)[0]
    text = text.strip()
    for sc, ec in [('{', '}'), ('[', ']')]:
        s = text.find(sc)
        e = text.rfind(ec)
        if s != -1 and e != -1:
            try:
                return json.loads(text[s:e+1])
            except Exception:
                continue
    return None