"""
script_parser.py — 剧本切分

三步走：
  Step 1: Segment 划分
    有scenes（Tab0输出）→ 跳过LLM，直接转换
    无scenes → 1次LLM切segment
  Step 2: Beat 切分（每个segment 3次串行LLM调用）
    2A: 只切分，确定raw_text边界和type
    2B: 填写字段（characters/scene/speaker等）
    2C: type校验，输出修正列表
  Step 3: 全文道具提取（1次独立LLM调用）

以scene为单位：
  beat的segment字段 = scene_id
  grid_map的key = scene_id
  Tab5宫格生图以scene为单位

算法后处理：
  _dedup_beats()             去重（同raw_text只保留line）
  _normalize_scene()         场景归一（同segment内统一scene）
  _clean_props()             道具黑名单过滤
  _assign_props_to_beats()   把Step3道具分配到beat
  _renumber()                统一编号
"""

import json
import re
import threading
import os
from datetime import datetime
from llm import call_llm

# ── 调试日志 ──────────────────────────────────────────────────

_debug_log: list[dict] = []
_debug_enabled: bool = True


def enable_debug(enabled: bool = True):
    global _debug_enabled
    _debug_enabled = enabled


def _log(step: str, system: str, prompt: str, response: str, extra: dict = None):
    if not _debug_enabled:
        return
    entry = {
        "step":     step,
        "system":   system,
        "prompt":   prompt,
        "response": response,
    }
    if extra:
        entry.update(extra)
    _debug_log.append(entry)


def save_debug_log(path: str = None):
    if not _debug_log:
        return None
    if path is None:
        ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(os.path.dirname(__file__), f"debug_parse_{ts}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(_debug_log, f, ensure_ascii=False, indent=2)
    print(f"[debug] 日志已保存：{path}（共 {len(_debug_log)} 条记录）")
    return path


def clear_debug_log():
    global _debug_log
    _debug_log = []


from prompts import (
    SEGMENT_SPLIT_SYSTEM, SEGMENT_SPLIT_USER,
    BEAT_SPLIT_SYSTEM,    BEAT_SPLIT_USER,
    BEAT_FIELD_SYSTEM,    BEAT_FIELD_USER,
    BEAT_VERIFY_SYSTEM,   BEAT_VERIFY_USER,
    BEAT_QC_SYSTEM,       BEAT_QC_USER,
    PROP_EXTRACT_SYSTEM,  PROP_EXTRACT_USER,
    BEAT_SUPPLEMENT_SYSTEM, BEAT_SUPPLEMENT_USER,
)


# ── 宫格规格 ─────────────────────────────────────────────────

GRID_RULES = [
    (4,  2, 2),
    (9,  3, 3),
    (16, 4, 4),
    (25, 5, 5),
]
MAX_GRID = 25

PROP_BLACKLIST = {
    "椅子","太师椅","席垫","案几","桌子","床","榻","蒲团","扶手","矮几",
    "屏风","博古架","衣架","铜盆","木桶",
    "香炉","灵位","牌位","灵牌","白幡","蜡烛","白烛","长明灯","纸钱",
    "供桌","神龛","佛珠","挽幛","灵柩","香案","祭品","冥纸",
    "柱子","门","窗","帘子","牌匾","窗棂","影壁","门槛","格窗",
    "台阶","廊柱","门楣","门帘","珠帘","石阶","青砖","门板",
    "衣袍","衣袖","素帕","头巾","面纱","孝服","白衣","丧服",
    "披风","斗篷","腰带","绣鞋","靴子","发冠","头饰",
    "香烟","灯影","尘埃","日光","树影","光线","香灰","灰烬","烟雾",
    "烛火","烛光","灯火","月光","星光","晨光","暮色","夜色",
    "风","雨","雪","霜","露","雾","云","落叶","枯叶",
    "檀香","拐杖","手杖","腰牌","发簪","帕子","扇子","玉佩","香囊",
    "荷包","护身符","手镯","耳环","钗子","步摇",
}


def get_grid_size(beat_count: int) -> int:
    for max_beats, rows, cols in GRID_RULES:
        if beat_count <= max_beats:
            return rows * cols
    return MAX_GRID


def get_grid_shape(beat_count: int) -> tuple[int, int]:
    for max_beats, rows, cols in GRID_RULES:
        if beat_count <= max_beats:
            return rows, cols
    return 5, 5


def get_grid_shape_for_beats(n: int) -> tuple[int, int]:
    return get_grid_shape(n)


# ── 主入口 ────────────────────────────────────────────────────

def parse_script(
    raw_text: str,
    story_tone: str = "",
    char_names: list[str] | None = None,
    scenes: list[dict] | None = None,
) -> tuple[list[dict], dict, list[dict]]:
    """
    主入口
    返回：(beats列表, grid_map, prop_list)

    scenes：Tab0 Step-F场景细分输出
      有则跳过Step1（LLM切segment），直接用scene_text切beat
      无则走原有流程，LLM重新切segment

    以scene为单位：
      beat的segment字段 = scene_id
      grid_map的key = scene_id
      Tab5宫格生图以scene为单位
    """
    raw_text   = raw_text.strip()
    story_tone = story_tone.strip() or "（未提供故事论调）"

    clear_debug_log()

    # ── Step1：Segment划分 ────────────────────────────────────
    if scenes:
        segments = _convert_scenes_to_segments(scenes)
        if not segments:
            raise ValueError("scenes转换失败，请检查Tab0场景细分结果")
        # 道具提取用scene_text拼接（已是漫剧化文本，信息更完整）
        raw_text_for_props = "\n\n".join(
            s.get("scene_text", "") for s in scenes if s.get("scene_text")
        )
    else:
        segments = _split_segments(raw_text, story_tone)
        if not segments:
            raise ValueError("Segment 划分失败")
        raw_text_for_props = raw_text

    # ── Step2：Beat切分（2A→2B→2C，segment间并发）────────────
    all_beats = _split_beats_parallel(segments, story_tone, char_names=char_names)
    if not all_beats:
        raise ValueError("Beat 切分失败")

    # ── 算法后处理 ────────────────────────────────────────────
    all_beats = _dedup_beats(all_beats)
    all_beats = _renumber(all_beats)

    # ── Step3：Beat补全到宫格格数（以scene/segment为单位，并发）
    all_beats = _supplement_beats_parallel(all_beats, story_tone)
    all_beats = _renumber(all_beats)

    # ── Step2D：LLM质检 ───────────────────────────────────────
    all_beats = _qc_beats(all_beats)

    # ── Step4：全文道具提取 ───────────────────────────────────
    prop_list = _extract_props(raw_text_for_props, segments)

    # 匹配dialogue_structure_ref
    all_beats = assign_dialogue_structure_refs(all_beats)

    # ── 强制清理：action类型的characters ──────────────────
    # 规则：raw_text主语/主体是具名人物 → 保留；主语是环境/物品/声音 → 清空
    # 用简单启发式：raw_text里出现了该角色名 → 保留，否则清空
    # 注意：这比"主语判断"更保守，但误伤少
    # 极少数边界情况（如"父子并肩"）由LLM自行判断，不强制干预
    for b in all_beats:
        if b.get("type") == "action":
            text  = b.get("raw_text", "")
            chars = b.get("characters", [])
            # 只保留名字出现在raw_text里的角色
            # 或者raw_text里有"父子/两人/他/她"等代词时保留全部
            has_pronoun = any(w in text for w in ["父子", "两人", "二人", "他们", "众人"])
            if has_pronoun:
                pass  # 有代词，保留characters不动
            else:
                b["characters"] = [c for c in chars if c in text]

    grid_map = _build_grid_map(all_beats)

    save_debug_log()

    return all_beats, grid_map, prop_list


# ════════════════════════════════════════════════════════════
# scenes → segments 转换（新路径）
# ════════════════════════════════════════════════════════════

def _convert_scenes_to_segments(scenes: list[dict]) -> list[dict]:
    """
    把Tab0 Step-F的scenes列表转成script_parser内部格式。

    数据结构：
      segment = 叙事模块（module）
      scene   = segment下的空间单元
      beat    = scene下的分镜

    字段对齐：
      segment_id         ← scene_id（Tab5以scene为单位生图，所以用scene_id作分组key）
      segment_desc       ← scene_name
      narrative_function ← segment_name（所属叙事模块名）
      text               ← scene_text
      scene_id           ← scene_id（明确标注属于哪个scene）
      scene_name         ← scene_name
      parent_segment_id  ← segment_id（明确标注属于哪个叙事模块）
      parent_segment_name← segment_name（所属叙事模块名）
      scene              ← scene_name（beat的scene字段）
      space_desc         ← space_desc
      hook_type          ← hook_type
    """
    result = []
    for s in scenes:
        text = s.get("scene_text", "").strip()
        if not text:
            continue
        result.append({
            # Tab5分组用（segment=scene_id）
            "segment_id":          int(s.get("scene_id", len(result) + 1)),
            "segment_desc":        str(s.get("scene_name", f"场景{len(result)+1}")),
            "narrative_function":  str(s.get("segment_name", s.get("module_name", "建立"))),
            "text":                text,
            # 层级字段（beat继承）
            "scene_id":            int(s.get("scene_id", len(result) + 1)),
            "scene_name":          str(s.get("scene_name", "")),
            "parent_segment_id":   int(s.get("segment_id", 0)),
            "parent_segment_name": str(s.get("segment_name", s.get("module_name", ""))),
            # 其他beat继承字段
            "scene":               str(s.get("scene_name", "")),
            "space_desc":          str(s.get("space_desc", "")),
            "hook_type":           str(s.get("hook_type", "null")),
        })
    return result


# ════════════════════════════════════════════════════════════
# Step 1：Segment 划分（原有LLM路径）
# ════════════════════════════════════════════════════════════

def _split_segments(raw_text: str, story_tone: str) -> list[dict]:
    prompt = (SEGMENT_SPLIT_USER
        .replace("{story_tone}", story_tone)
        .replace("{script}",     raw_text)
    )
    result   = call_llm(prompt, SEGMENT_SPLIT_SYSTEM)
    _log("Step1_Segment划分", SEGMENT_SPLIT_SYSTEM, prompt, result)
    segments = _extract_json_array(result)

    valid = []
    for i, seg in enumerate(segments):
        if not isinstance(seg, dict) or not seg.get("text", "").strip():
            continue
        valid.append({
            "segment_id":         int(seg.get("segment_id", i + 1)),
            "segment_desc":       str(seg.get("segment_desc", f"段落{i+1}")),
            "narrative_function": str(seg.get("narrative_function", "建立")),
            "text":               seg["text"].strip(),
            "scene":              "",
            "space_desc":         "",
            "hook_type":          "null",
        })
    return valid


# ════════════════════════════════════════════════════════════
# Step 2：Beat 切分（2A→2B→2C 串行，segment间并发）
# ════════════════════════════════════════════════════════════

def _split_beats_parallel(
    segments: list[dict],
    story_tone: str,
    char_names: list[str] | None = None,
    batch_size: int = 3,
) -> list[dict]:
    """
    segment间分批并发，每批最多batch_size个同时跑。
    避免全部并发导致SSL连接被截断。
    """
    results = [None] * len(segments)
    lock    = threading.Lock()

    def worker(idx, seg, prev_seg):
        try:
            beats = _split_beats_single(seg, prev_seg, story_tone, char_names)
            with lock:
                results[idx] = beats
        except Exception as e:
            print(f"[parser] segment {seg['segment_id']} 切分失败: {e}")
            with lock:
                results[idx] = []

    # 分批执行，每批batch_size个
    for batch_start in range(0, len(segments), batch_size):
        batch = segments[batch_start:batch_start + batch_size]
        threads = []
        for i, seg in enumerate(batch):
            global_idx = batch_start + i
            prev_seg   = segments[global_idx - 1] if global_idx > 0 else None
            t = threading.Thread(
                target=worker,
                args=(global_idx, seg, prev_seg),
                daemon=True,
            )
            threads.append(t)
        for t in threads: t.start()
        for t in threads: t.join()
        # 批次间短暂间隔，让连接池有时间释放
        if batch_start + batch_size < len(segments):
            import time as _time
            _time.sleep(1)

    all_beats = []
    for beats in results:
        if beats:
            all_beats.extend(beats)
    return all_beats


def _split_beats_single(
    seg: dict,
    prev_seg: dict | None,
    story_tone: str,
    char_names: list[str] | None = None,
) -> list[dict]:
    """对单个segment做2A→2B→2C三步切分"""

    prev_last = ""
    if prev_seg:
        prev_last = prev_seg["text"].strip().split("\n")[-1][:100]

    seg_id   = seg["segment_id"]
    seg_desc = seg["segment_desc"]
    seg_func = seg.get("narrative_function", "建立")
    seg_text = seg["text"]

    # ── 2A：只切分，只判断type ────────────────────────────
    char_names_str = "、".join(char_names) if char_names else "（未提供）"
    char_hint = (
        f"\n\n# 已知角色名单（台词识别参考）\n{char_names_str}\n"
        f"以上角色名后接冒号或说话动词，必须识别为line类型。"
    )

    # 构建dialogue_structure_hint（供切分参考）
    ds_list = seg.get("dialogue_structure", [])
    if ds_list:
        ds_lines = []
        for d in ds_list:
            t = d.get("type", "dialogue")
            spk = d.get("speaker", "")
            trigger = d.get("trigger", "")
            ds_lines.append(f"  {d.get('order','')}.【{t}】{spk} — {trigger}")
        dialogue_structure_hint = "\n".join(ds_lines)
    else:
        dialogue_structure_hint = "（无台词结构规划，按原文判断）"

    prompt_2a = (BEAT_SPLIT_USER
        .replace("{segment_desc}",            seg_desc)
        .replace("{narrative_function}",      seg_func)
        .replace("{prev_last_raw}",           prev_last or "（无，这是第一段）")
        .replace("{dialogue_structure_hint}", dialogue_structure_hint)
        .replace("{segment_text}",            seg_text)
    ) + char_hint

    raw_2a   = call_llm(prompt_2a, BEAT_SPLIT_SYSTEM)
    _log("Step2A_Beat切分边界", BEAT_SPLIT_SYSTEM, prompt_2a, raw_2a,
         {"segment_id": seg_id, "segment_desc": seg_desc})
    beats_2a = _extract_json_array(raw_2a)

    if not beats_2a:
        return []

    # ── 2B：填写字段 ─────────────────────────────────────
    beats_simple = json.dumps(beats_2a, ensure_ascii=False, indent=2)
    # 台词结构摘要（供audio_type填写参考）
    if ds_list:
        ds_short_lines = [
            f"{d.get('order','')}.{d.get('speaker','')}[{d.get('type','')}]"
            for d in ds_list
        ]
        ds_short = " / ".join(ds_short_lines)
    else:
        ds_short = "无规划"

    prompt_2b = (BEAT_FIELD_USER
        .replace("{segment_desc}",              seg_desc)
        .replace("{segment_id}",                str(seg_id))
        .replace("{beats_simple}",              beats_simple)
        .replace("{dialogue_structure_hint_short}", ds_short)
    )
    raw_2b   = call_llm(prompt_2b, BEAT_FIELD_SYSTEM)
    _log("Step2B_字段填写", BEAT_FIELD_SYSTEM, prompt_2b, raw_2b,
         {"segment_id": seg_id, "segment_desc": seg_desc})
    beats_2b = _extract_json_array(raw_2b)

    if not beats_2b:
        beats_2b = []
        for b in beats_2a:
            beats_2b.append({
                **b,
                "subtype":      None,
                "characters":   [],
                "speaker":      None,
                "scene":        seg.get("scene_name", seg.get("scene", None)),
                "period_map":   {},
                "segment":      seg_id,
                "segment_desc": seg_desc,
                "type_flag":    "original",
                "space_desc":   seg.get("space_desc", ""),
                "hook_type":    seg.get("hook_type", "null"),
            })

    # 确保segment字段正确，继承层级字段
    for b in beats_2b:
        if isinstance(b, dict):
            # Tab5分组key（值=scene_id）
            b["segment"]      = seg_id
            b["segment_desc"] = seg.get("scene_name", seg_desc)
            b.setdefault("period_map", {})
            b.setdefault("type_flag", "original")
            # 层级字段
            b.setdefault("scene_name",          seg.get("scene_name", seg_desc))
            b.setdefault("parent_segment_id",   seg.get("parent_segment_id", 0))
            b.setdefault("parent_segment_name", seg.get("parent_segment_name", ""))
            # scene强制用Tab0的scene_name（不信任LLM填的值）
            b["scene"] = seg.get("scene_name", seg.get("scene", ""))
            b.setdefault("space_desc",         seg.get("space_desc", ""))
            b.setdefault("hook_type",          seg.get("hook_type", "null"))
            b.setdefault("dialogue_structure", seg.get("dialogue_structure", []))
            # 新type体系兜底和修正
            btype = b.get("type", "")
            # 旧type迁移（shot→action, line→按subtype/audio_type判断）
            if btype == "shot":
                b["type"] = "action"
                btype = "action"
            elif btype == "line":
                bsub = b.get("subtype", "")
                if bsub == "inner":        b["type"] = "inner"
                elif bsub == "narration":  b["type"] = "narration"
                else:                      b["type"] = "dialogue"
                btype = b["type"]
            # audio_type和type严格对应
            type_to_audio = {
                "dialogue":  "dialogue",
                "action":    "sfx",
                "narration": "narration",
                "inner":     "inner",
                "caption":   "sfx",
            }
            if not b.get("audio_type") or b["audio_type"] not in type_to_audio.values():
                b["audio_type"] = type_to_audio.get(btype, "sfx")
            # line/action字段兜底
            b.setdefault("line",   "")
            b.setdefault("action", "")
            # action类型：只保留raw_text里真正出现的角色名
            if b.get("type") == "action":
                text  = b.get("raw_text", "")
                chars = b.get("characters", [])
                b["characters"] = [c for c in chars if c in text]
            # dialogue_structure_ref默认-1（未匹配）
            b.setdefault("dialogue_structure_ref", -1)

    # ── 2C：type校验 ─────────────────────────────────────
    beats_json_str = json.dumps(beats_2b, ensure_ascii=False, indent=2)
    prompt_2c = BEAT_VERIFY_USER.replace("{beats_json}", beats_json_str)
    raw_2c    = call_llm(prompt_2c, BEAT_VERIFY_SYSTEM)
    _log("Step2C_type校验", BEAT_VERIFY_SYSTEM, prompt_2c, raw_2c,
         {"segment_id": seg_id, "segment_desc": seg_desc,
          "fixes_applied": len(_extract_json_array(raw_2c))})
    fixes = _extract_json_array(raw_2c)

    if fixes:
        fix_map = {f["order"]: f for f in fixes if isinstance(f, dict) and "order" in f}
        for b in beats_2b:
            order = b.get("order")
            if order in fix_map:
                f = fix_map[order]
                if "fix_type" in f:
                    b["type"] = f["fix_type"]
                if "fix_subtype" in f:
                    b["subtype"] = f["fix_subtype"]
                if "fix_speaker" in f:
                    b["speaker"] = f["fix_speaker"]
                if f.get("duplicate"):
                    b["_duplicate"] = True

    beats_2b = [b for b in beats_2b if not b.get("_duplicate")]

    # 算法修复：type格式
    for b in beats_2b:
        t = b.get("type", "")
        if isinstance(t, str) and "/" in t:
            parts = t.split("/")
            b["type"] = parts[0].strip()
            if len(parts) > 1 and not b.get("subtype"):
                b["subtype"] = parts[1].strip()

    # 算法修复：narration的speaker
    for b in beats_2b:
        if (b.get("type") in ("dialogue", "narration", "inner") and
                b.get("subtype") == "narration" and
                not b.get("speaker")):
            b["speaker"] = "旁白"

    # 算法修复：characters泛称过滤
    GENERIC_NAMES = {
        "众人","下人","丫鬟","小厮","小厮们","众嫂嫂","嫂嫂们",
        "林家众人","其他林家众人","其他众人","其余众人","在场众人",
        "家丁","侍卫","侍女","婆子","管家","掌柜","店小二",
        "路人","百姓","士兵","将士","宫人","内侍","太监","宫女",
        "旁白","VO","narration","narrator",
        "七位孙媳","七位嫂嫂","众嫂","各位嫂嫂","诸位",
        "老太太","老太君","族老","长辈","众长辈",
    }
    GENERIC_PATTERNS_START = ("其他", "众", "所有", "各位", "诸")
    GENERIC_PATTERNS_END   = ("们", "等人", "若干", "数人", "多人", "众人")
    GENERIC_CONTAINS       = ("家众", "林家", "府中", "厅内", "屋内")
    for b in beats_2b:
        chars = b.get("characters") or []
        cleaned = []
        for c in chars:
            if c in GENERIC_NAMES:
                continue
            if any(c.startswith(p) for p in GENERIC_PATTERNS_START):
                continue
            if any(c.endswith(p) for p in GENERIC_PATTERNS_END):
                continue
            if any(p in c for p in GENERIC_CONTAINS):
                continue
            cleaned.append(c)
        b["characters"] = cleaned

    return [b for b in beats_2b if isinstance(b, dict)]


# ════════════════════════════════════════════════════════════
# 算法后处理
# ════════════════════════════════════════════════════════════

def _dedup_beats(beats: list[dict]) -> list[dict]:
    seen: dict[str, dict] = {}
    for b in beats:
        rt = (b.get("raw_text") or "").strip()
        if not rt:
            continue
        if rt not in seen:
            seen[rt] = b
        else:
            existing = seen[rt]
            if existing.get("type") == "action" and b.get("type") in ("dialogue", "narration", "inner"):
                seen[rt] = b
    result = []
    added  = set()
    for b in beats:
        rt = (b.get("raw_text") or "").strip()
        if rt and seen.get(rt) is b and rt not in added:
            result.append(b)
            added.add(rt)
        elif not rt:
            result.append(b)
    return result


def _normalize_scene_per_segment(beats: list[dict]) -> list[dict]:
    """
    同一segment内scene统一。
    有scenes路径时scene已从segment继承，不需要统一。
    无scenes路径时按出现次数最多的scene统一。
    """
    from collections import Counter
    seg_map: dict[int, list] = {}
    for b in beats:
        sid = b.get("segment", 0)
        seg_map.setdefault(sid, []).append(b)

    for sid, seg_beats in seg_map.items():
        # 如果所有beat的scene都一样，跳过
        scenes = [b.get("scene") for b in seg_beats if b.get("scene")]
        if not scenes:
            continue
        unique = set(scenes)
        if len(unique) == 1:
            continue
        # 有不一致，取最多的
        most_common = Counter(scenes).most_common(1)[0][0]
        for b in seg_beats:
            if b.get("scene") and b["scene"] != most_common:
                b["scene"] = most_common

    return beats


def _clean_props(beats: list[dict]) -> list[dict]:
    for b in beats:
        props = b.get("props") or []
        b["props"] = [p for p in props if p not in PROP_BLACKLIST]
    return beats


# ════════════════════════════════════════════════════════════
# Step 2D：LLM质检
# ════════════════════════════════════════════════════════════

def _qc_beats(beats: list[dict]) -> list[dict]:
    """
    按segment分批质检，每批独立调用LLM。
    避免全量beats塞进一次调用导致token超限。
    """
    if not beats:
        return beats

    # 按segment分组
    seg_map: dict[int, list] = {}
    seg_order = []
    for b in beats:
        sid = b.get("segment", 0)
        if sid not in seg_map:
            seg_map[sid] = []
            seg_order.append(sid)
        seg_map[sid].append(b)

    all_fixed = []
    for sid in seg_order:
        seg_beats  = seg_map[sid]
        beats_json = json.dumps(seg_beats, ensure_ascii=False, indent=2)
        prompt     = BEAT_QC_USER.replace("{beats_json}", beats_json)
        result     = call_llm(prompt, BEAT_QC_SYSTEM)
        _log("Step2D_分段质检", BEAT_QC_SYSTEM, prompt, result,
             {"segment_id": sid, "beats_count": len(seg_beats)})
        fixed = _extract_json_array(result)

        if len(fixed) != len(seg_beats):
            print(f"[qc] segment {sid} 质检数量不符（期望{len(seg_beats)},实际{len(fixed)}），保留原始")
            all_fixed.extend(seg_beats)
            continue

        # 校验raw_text不被修改
        raw_mismatch = False
        for orig, fix in zip(seg_beats, fixed):
            if orig.get("raw_text") != fix.get("raw_text"):
                print(f"[qc] segment {sid} raw_text被修改，保留原始: {orig.get('beat_id')}")
                raw_mismatch = True
                break

        if raw_mismatch:
            all_fixed.extend(seg_beats)
            continue

        # 修正后保留关键字段
        for orig, fix in zip(seg_beats, fixed):
            fix["beat_id"]               = orig["beat_id"]
            fix["order"]                 = orig["order"]
            fix["segment"]               = orig["segment"]
            fix["segment_desc"]          = orig["segment_desc"]
            fix["type_flag"]             = orig["type_flag"]
            fix["scene"]                 = orig["scene"]
            fix["scene_name"]            = orig["scene_name"]
            fix["space_desc"]            = orig.get("space_desc", "")
            fix["hook_type"]             = orig.get("hook_type", "null")
            fix["dialogue_structure"]    = orig.get("dialogue_structure", [])
            fix["dialogue_structure_ref"]= orig.get("dialogue_structure_ref", -1)
            fix["parent_segment_id"]     = orig.get("parent_segment_id", 0)
            fix["parent_segment_name"]   = orig.get("parent_segment_name", "")
            fix.setdefault("dialogues",  [])
            fix.setdefault("audio_type", orig.get("audio_type", "sfx"))

        all_fixed.extend(fixed)

    return all_fixed


# ════════════════════════════════════════════════════════════
# Step 3：全文道具提取
# ════════════════════════════════════════════════════════════

def _extract_props(
    raw_text: str,
    segments: list[dict],
) -> list[dict]:
    seg_descs = "\n".join(
        f"Segment {s['segment_id']}：{s['segment_desc']}"
        for s in segments
    )
    prompt = (PROP_EXTRACT_USER
        .replace("{segment_descs}", seg_descs)
        .replace("{full_script}",   raw_text)
    )
    result = call_llm(prompt, PROP_EXTRACT_SYSTEM)
    _log("Step4_道具提取", PROP_EXTRACT_SYSTEM, prompt, result)
    props  = _extract_json_array(result)
    return [p for p in props if isinstance(p, dict) and p.get("prop_name")]


def _assign_props_to_beats(
    beats: list[dict],
    prop_list: list[dict],
) -> list[dict]:
    if not prop_list:
        return beats
    valid_props = [
        p for p in prop_list
        if p.get("prop_name") and p["prop_name"] not in PROP_BLACKLIST
    ]
    for b in beats:
        rt = b.get("raw_text", "")
        b["props"] = []
        for prop in valid_props:
            name = prop.get("prop_name", "")
            if name and name in rt:
                b["props"] = [name]
                break
    return beats


# ════════════════════════════════════════════════════════════
# Beat 补全到宫格格数（以scene/segment为单位）
# ════════════════════════════════════════════════════════════

def _supplement_beats_parallel(
    all_beats: list[dict],
    story_tone: str,
) -> list[dict]:
    seg_map: dict[int, list] = {}
    for b in all_beats:
        sid = b.get("segment", 0)
        seg_map.setdefault(sid, []).append(b)

    results = {}
    lock    = threading.Lock()

    def worker(sid, beats):
        try:
            supplemented = _supplement_single_segment(beats, story_tone)
            with lock:
                results[sid] = supplemented
        except Exception as e:
            print(f"[parser] segment/scene {sid} 补全失败: {e}")
            with lock:
                results[sid] = beats

    # 分批并发，每批最多3个
    batch_size = 3
    seg_items  = list(seg_map.items())
    for batch_start in range(0, len(seg_items), batch_size):
        batch   = seg_items[batch_start:batch_start + batch_size]
        threads = [
            threading.Thread(target=worker, args=(sid, beats), daemon=True)
            for sid, beats in batch
        ]
        for t in threads: t.start()
        for t in threads: t.join()
        if batch_start + batch_size < len(seg_items):
            import time as _time
            _time.sleep(1)

    final = []
    for sid in sorted(results.keys()):
        final.extend(results[sid])
    return final


def _supplement_single_segment(
    beats: list[dict],
    story_tone: str,
) -> list[dict]:
    current = len(beats)
    if current >= MAX_GRID:
        return beats
    target = get_grid_size(current)
    if current == target:
        return beats

    insert_count = target - current
    seg_id   = beats[0].get("segment", 1)
    seg_desc = beats[0].get("segment_desc", "")

    beats_summary = "\n".join(
        f"{i+1}. [{b.get('type','action')}] {b.get('raw_text','')}"
        for i, b in enumerate(beats)
    )

    prompt = (BEAT_SUPPLEMENT_USER
        .replace("{story_tone}",    story_tone)
        .replace("{insert_count}",  str(insert_count))
        .replace("{target_count}",  str(target))
        .replace("{beats_summary}", beats_summary)
        .replace("{segment_id}",    str(seg_id))
        .replace("{segment_desc}",  seg_desc)
    )

    result    = call_llm(prompt, BEAT_SUPPLEMENT_SYSTEM)
    _log("Step3_Beat补全", BEAT_SUPPLEMENT_SYSTEM, prompt, result,
         {"segment_id": seg_id, "original_count": current,
          "target_count": target, "insert_count": insert_count})
    new_beats = _extract_json_array(result)
    valid     = [b for b in new_beats if isinstance(b, dict)]

    default_scene           = beats[0].get("scene")               if beats else None
    default_space           = beats[0].get("space_desc", "")
    default_hook            = beats[0].get("hook_type", "null")
    default_scene_name      = beats[0].get("scene_name", seg_desc) if beats else seg_desc
    default_parent_seg_id   = beats[0].get("parent_segment_id", 0) if beats else 0
    default_parent_seg_name = beats[0].get("parent_segment_name", "") if beats else ""

    PLACEHOLDER_WORDS = {"与segment一致", "与前后beat一致", "与前后", "继承"}

    for b in valid:
        b["segment"]      = seg_id
        b["segment_desc"] = seg_desc
        b.setdefault("type_flag",                "supplement")
        b.setdefault("period_map",               {})
        b.setdefault("space_desc",               default_space)
        b.setdefault("hook_type",                default_hook)
        b.setdefault("scene_name",               default_scene_name)
        b.setdefault("parent_segment_id",        default_parent_seg_id)
        b.setdefault("parent_segment_name",      default_parent_seg_name)
        b.setdefault("dialogue_structure_ref",   -1)
        b.setdefault("dialogues",                [])

        # scene强制用scene_name
        b["scene"] = default_scene_name or default_scene

        # audio_type兜底
        if not b.get("audio_type"):
            btype = b.get("type", "action")
            bsub  = b.get("subtype", "")
            if btype in ("dialogue", "narration", "inner"):
                if bsub == "inner":       b["audio_type"] = "inner"
                elif bsub == "narration": b["audio_type"] = "narration"
                else:                     b["audio_type"] = "dialogue"
            else:
                b["audio_type"] = "sfx"

        t = b.get("type", "")
        if "/" in str(t):
            parts = t.split("/")
            b["type"]    = parts[0].strip()
            b["subtype"] = parts[1].strip() if len(parts) > 1 else b.get("subtype")

    if len(valid) != target:
        print(f"[parser] segment/scene {seg_id} 补全数量不符 (期望{target},实际{len(valid)})")
        return beats

    return valid




# ════════════════════════════════════════════════════════════
# 台词提取（独立步骤，Tab1按钮触发）
# ════════════════════════════════════════════════════════════

_DIALOGUE_EXTRACT_SYSTEM = """你是台词提取工具，从beat的raw_text里提取所有台词，输出结构化JSON。

# 提取规则

对白（dialogue）：
  格式：角色名："台词内容" 或 角色名：'台词内容'
  提取：speaker=角色名，line=引号内内容，type=dialogue

内心独白（inner）：
  格式：角色名（内心）："内容"
  提取：speaker=角色名，line=引号内内容，type=inner

旁白（narration）：
  格式：旁白："内容" 或 VO："内容"
  提取：speaker=旁白，line=引号内内容，type=narration

# 输出规则
- 按原文出现顺序输出
- 只提取有引号的台词，不推断，不补充
- 纯动作描写（无引号台词）→ 输出空数组[]
- 每条台词line字段≤30字，超过截断

输出严格JSON数组，不加任何解释：
[{"speaker": "角色名", "line": "台词内容", "type": "dialogue|inner|narration"}]"""


def extract_dialogues_single(beat: dict) -> list[dict]:
    """
    对单个beat调用LLM提取台词。
    返回dialogues列表。
    type=dialogue/narration/inner时处理。
    """
    btype      = beat.get("type", "")
    audio_type = beat.get("audio_type", "")
    has_dialogue_audio = audio_type in ("dialogue", "inner", "narration")

    # shot类型但audio_type是dialogue/inner/narration → 也提取
    if btype not in ("dialogue", "narration", "inner") and not has_dialogue_audio:
        return []

    raw_text = beat.get("raw_text", "").strip()
    if not raw_text:
        return []

    prompt = f"请提取以下raw_text中的所有台词：\n\n{raw_text}"
    try:
        result = call_llm(prompt, _DIALOGUE_EXTRACT_SYSTEM)
        parsed = _extract_json_array(result)
        # 校验格式
        valid = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            if not item.get("speaker") or not item.get("line"):
                continue
            if item.get("type") not in ("dialogue", "inner", "narration"):
                item["type"] = beat.get("subtype", "dialogue")
            valid.append({
                "speaker": str(item["speaker"]),
                "line":    str(item["line"])[:30],
                "type":    item["type"],
            })
        return valid
    except Exception as e:
        print(f"[parser] extract_dialogues {beat.get('beat_id')}: {e}")
        return []


def extract_dialogues_batch(
    beats: list[dict],
    batch_size: int = 5,
) -> list[dict]:
    """
    批量并发提取所有line beat的台词。
    每批batch_size个并发，批次间间隔0.5s。
    返回更新后的beats列表（dialogues字段已写入）。
    """
    import time as _time

    # 包含type=line，以及type=shot但audio_type有台词的beat
    line_beats = [
        b for b in beats
        if b.get("type") in ("dialogue", "narration", "inner")
        or b.get("audio_type") in ("dialogue", "inner", "narration")
    ]
    if not line_beats:
        return beats

    results  = {}
    lock     = threading.Lock()

    def worker(beat):
        bid  = beat.get("beat_id", "")
        dlgs = extract_dialogues_single(beat)
        with lock:
            results[bid] = dlgs

    # 分批并发
    for batch_start in range(0, len(line_beats), batch_size):
        batch   = line_beats[batch_start:batch_start + batch_size]
        threads = [
            threading.Thread(target=worker, args=(b,), daemon=True)
            for b in batch
        ]
        for t in threads: t.start()
        for t in threads: t.join()
        if batch_start + batch_size < len(line_beats):
            _time.sleep(0.5)

    # 写回beats
    for b in beats:
        bid = b.get("beat_id", "")
        if bid in results:
            b["dialogues"] = results[bid]
        else:
            b.setdefault("dialogues", [])

    return beats

# ════════════════════════════════════════════════════════════
# 工具函数
# ════════════════════════════════════════════════════════════

def _renumber(beats: list[dict]) -> list[dict]:
    for i, beat in enumerate(beats, 1):
        beat["beat_id"] = f"beat_{i:03d}"
        beat["order"]   = i
    return beats


def _build_grid_map(beats: list[dict]) -> dict:
    seg_counts: dict[int, int] = {}
    for b in beats:
        sid = b.get("segment", 0)
        seg_counts[sid] = seg_counts.get(sid, 0) + 1

    grid_map = {}
    for sid, count in seg_counts.items():
        rows, cols = get_grid_shape(count)
        grid_map[sid] = {
            "grid_size":  rows * cols,
            "rows":       rows,
            "cols":       cols,
            "beat_count": count,
        }
    return grid_map


def _extract_json_array(text: str) -> list:
    text = text.strip()
    text = re.sub(r"^```[a-z]*\n?", "", text)
    text = re.sub(r"\n?```$",       "", text)
    text = text.strip()
    start = text.find("[")
    end   = text.rfind("]")
    if start == -1 or end == -1:
        return []
    try:
        return json.loads(text[start:end + 1])
    except Exception:
        return []


# ════════════════════════════════════════════════════════════
# dialogue_structure_ref 匹配
# ════════════════════════════════════════════════════════════

def assign_dialogue_structure_refs(beats: list[dict]) -> list[dict]:
    """
    为每个line beat匹配dialogue_structure里对应的条目index。
    
    匹配策略（按优先级）：
    1. speaker相同 + audio_type相同 → 按顺序匹配
    2. audio_type相同 → 按顺序匹配
    3. 匹配不到 → 保留-1
    
    写入字段：dialogue_structure_ref（int，-1表示未匹配）
    """
    # 按segment分组处理
    seg_map: dict[int, list] = {}
    for b in beats:
        sid = b.get("segment", 0)
        seg_map.setdefault(sid, []).append(b)

    for sid, seg_beats in seg_map.items():
        # 取该segment的dialogue_structure
        ds = None
        for b in seg_beats:
            ds = b.get("dialogue_structure")
            if ds:
                break
        if not ds:
            continue

        # 只匹配line beat
        line_beats = [b for b in seg_beats if b.get("type") in ("dialogue", "narration", "inner")]
        if not line_beats:
            continue

        # 为每个ds条目维护一个已使用标记
        used = [False] * len(ds)

        for b in line_beats:
            b_speaker    = b.get("speaker", "")
            b_audio_type = b.get("audio_type", "")

            best_idx = -1

            # 优先：speaker + audio_type 都匹配
            for i, d in enumerate(ds):
                if used[i]:
                    continue
                d_type    = d.get("type", "")
                d_speaker = d.get("speaker", "")
                if d_speaker == b_speaker and d_type == b_audio_type:
                    best_idx = i
                    break

            # 次优：只匹配audio_type
            if best_idx == -1:
                for i, d in enumerate(ds):
                    if used[i]:
                        continue
                    d_type = d.get("type", "")
                    if d_type == b_audio_type:
                        best_idx = i
                        break

            if best_idx >= 0:
                b["dialogue_structure_ref"] = best_idx
                used[best_idx] = True

    return beats


# ════════════════════════════════════════════════════════════
# Beat级别角色分配
# ════════════════════════════════════════════════════════════

NON_CHARS = {
    "旁白", "narrator", "Narrator", "VO", "旁白/VO",
    "画外音", "os旁白", "系统", "解说", "独白", "",
}


def resolve_beat_characters(
    beats: list,
    scenes: list,
    chars_init: dict,
) -> list:
    """
    统一分配每个beat的characters字段。
    优先级（从高到低）：
      1. beat自带characters（LLM识别），过滤不在chars_init里的名字
      2. beat.speaker直接在chars_init里
      3. dialogue_structure_ref精确定位到台词的speaker
      4. type=shot的beat从scene.chars_in_scene继承
      5. []（纯环境/旁白）

    所有角色名必须在chars_init里存在，过滤泛称。
    caption类型强制为[]。
    """
    valid_chars = set(chars_init.keys())

    # 建立scene级映射
    scene_chars: dict = {}
    scene_ds:    dict = {}
    for s in scenes:
        name = s.get("scene_name", "")
        if not name:
            continue
        scene_chars[name] = [
            c for c in s.get("chars_in_scene", [])
            if c in valid_chars
        ]
        scene_ds[name] = s.get("dialogue_structure", [])

    for b in beats:
        scene_name = b.get("scene_name") or b.get("scene", "")
        btype      = b.get("type", "")

        # caption不需要人物
        if btype == "caption":
            b["characters"] = []
            continue

        # 1. beat自带characters，过滤无效
        existing = [c for c in (b.get("characters") or []) if c in valid_chars]
        if existing:
            b["characters"] = existing
            continue

        # 2. beat.speaker直接在chars_init里
        speaker = b.get("speaker", "")
        if speaker and speaker not in NON_CHARS and speaker in valid_chars:
            b["characters"] = [speaker]
            continue

        # 3. dialogue_structure_ref精确定位
        ds_ref = b.get("dialogue_structure_ref", -1)
        if ds_ref >= 0:
            ds = scene_ds.get(scene_name, [])
            if ds_ref < len(ds):
                sp = ds[ds_ref].get("speaker", "")
                if sp and sp not in NON_CHARS and sp in valid_chars:
                    b["characters"] = [sp]
                    continue

        # 4. type=action从scene继承
        if btype == "action":
            inherited = scene_chars.get(scene_name, [])
            if inherited:
                b["characters"] = list(inherited)
                continue

        # 5. 空
        b["characters"] = []

    return beats