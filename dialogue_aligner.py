# ════════════════════════════════════════════════════════════
# dialogue_aligner.py
# 台词分配模块
#
# 流程：
#   原始剧本 → parse_raw_dialogues → 全局台词列表
#   按segment切分台词子列表
#   每个segment一次LLM调用（segment间并发）
#   结果存入 session_state["dialogue_state"]
#   Tab7 _extract_dialogue 直接读取
# ════════════════════════════════════════════════════════════

from __future__ import annotations
import re
import json
import asyncio
from collections import defaultdict
from llm import call_llm

# ────────────────────────────────────────────────────────────
# 原始剧本台词解析
# ────────────────────────────────────────────────────────────

def parse_raw_dialogues(raw_script: str) -> list[dict]:
    """
    从原始剧本提取所有台词，按出现顺序排列。

    支持格式：
      角色名（动作）：台词        → dialogue
      角色名（os）：内心独白      → inner
      角色名（OS）：内心独白      → inner
      VO：旁白                   → narration
      角色名：台词               → dialogue（最基本格式）

    返回：
      [{"speaker": str, "dialogue": str, "type": str}, ...]
    """
    results = []

    # 匹配顺序很重要：更精确的模式放前面
    patterns = [
        # 角色名（os/OS）：内心
        (r'^(.+?)[（(][oO][sS][）)][:：](.+)$', 'inner'),
        # VO：旁白
        (r'^[Vv][Oo][:：](.+)$', 'narration'),
        # 角色名（任意括号内容）：台词
        (r'^(.+?)[（(][^）)]+[）)][:：](.+)$', 'dialogue'),
        # 角色名：台词（2-8字角色名，避免误匹配长句）
        (r'^([^\s，。！？、…]{1,8})[:：](.+)$', 'dialogue'),
    ]

    for line in raw_script.splitlines():
        line = line.strip()
        if not line:
            continue
        for pattern, dtype in patterns:
            m = re.match(pattern, line)
            if m:
                if dtype == 'narration':
                    results.append({
                        "speaker":  "旁白",
                        "dialogue": m.group(1).strip(),
                        "type":     dtype,
                    })
                else:
                    speaker  = m.group(1).strip()
                    dialogue = m.group(2).strip()
                    # 过滤掉明显不是角色名的情况（含标点/过长）
                    if len(speaker) <= 8 and not re.search(r'[，。！？…]', speaker):
                        results.append({
                            "speaker":  speaker,
                            "dialogue": dialogue,
                            "type":     dtype,
                        })
                break

    return results


# ────────────────────────────────────────────────────────────
# 按 segment 切分台词子列表
# ────────────────────────────────────────────────────────────

def _split_by_segment(
    raw_dialogues: list[dict],
    seg_beats: dict[int, list],
) -> dict[int, list[dict]]:
    """
    把全局台词列表按 segment 顺序切分。
    每个 segment 按 line 类型 beat 数量 + 余量取台词。
    """
    queue  = list(raw_dialogues)
    result = {}

    for seg_id in sorted(seg_beats.keys()):
        beats = seg_beats[seg_id]
        # 统计有人物的 beat 数量作为估算基准
        char_beats = sum(1 for b in beats if b.get("characters"))
        take = max(char_beats + 3, 4)   # 余量3条，最少4条
        result[seg_id] = queue[:take]
        queue = queue[take:]

    return result


# ────────────────────────────────────────────────────────────
# Prompt 构建
# ────────────────────────────────────────────────────────────

def _build_system(chars: dict) -> str:
    # 人物台词风格描述
    style_lines = []
    for name, info in (chars or {}).items():
        personality  = info.get("personality", "")
        speech_style = info.get("speech_style", "")
        if personality or speech_style:
            style_lines.append(f"  {name}：{personality}；台词风格：{speech_style}")
    char_block = "\n".join(style_lines) if style_lines else "  （暂无人物风格档案）"

    return f"""# Role
你是漫剧台词分配专家，负责把原始剧本台词分配给对应的画面beat。

# 核心原则
台词来源唯一：所有台词必须来自"原始台词队列"，不能自行创作。
顺序消耗：台词队列按剧情顺序排列，从头开始匹配，每条台词只用一次。

# 分配规则

## 哪些beat需要台词
以下beat是候选，LLM判断是否分配：
  characters非空 且 subtype不是visual
  raw_text里含说话动词（道、说、问、答、冷声、低声、厉声、笑道）→ 强候选
  raw_text里含心理动词（心中、暗道、心想、梳理、盘算、冷笑）→ 强候选（inner类型）

## 哪些beat不分配台词
  characters=[]（空镜）→ 跳过
  subtype=visual → 跳过
  shot_size=LS或ELS → 通常跳过（画面太远）
  raw_text是纯场景描写，无人物行为 → 跳过

## 台词匹配逻辑
  从队列头部开始：
    队列下一条台词的speaker在beat的characters里 → 取出分配给这个beat
    不在characters里 → 这个beat暂不分配，台词留在队列等下一个beat
  如果队列为空 → 剩余beat全部标记none

## 微调原则（能不改就不改）
只允许以下微调：
  ① 自称统一：按人物风格档案统一自称（如"我"→"本宫"）
  ② 景别精简：CU/ECU且台词超过15字，可适当精简但不改语义
  ③ 标点节奏：根据情绪调整停顿符号
  禁止：改动实词、添加原文没有的信息、合并不同人物台词

# 人物台词风格档案
{char_block}

# 输出格式
严格JSON数组，每个beat输出一条，不加任何解释：
[{{
  "beat_id": "beat_001",
  "has_dialogue": true,
  "dialogue": "台词内容",
  "speaker": "角色名",
  "dialogue_type": "dialogue/inner/narration",
  "source": "original/adapted/none",
  "adapt_note": "微调说明，source=original时为空字符串"
}}]"""


def _build_user(
    segment_desc: str,
    dialogues:    list[dict],
    beats:        list[dict],
) -> str:
    # 格式化台词队列
    dialogue_lines = []
    for i, d in enumerate(dialogues, 1):
        dtype = "OS" if d["type"] == "inner" else ("VO" if d["type"] == "narration" else "")
        tag   = f"（{dtype}）" if dtype else ""
        dialogue_lines.append(f"  [{i}] {d['speaker']}{tag}：{d['dialogue']}")
    dialogue_block = "\n".join(dialogue_lines) if dialogue_lines else "  （本段无台词）"

    # 格式化beat列表
    beat_lines = []
    for b in beats:
        chars = "、".join(b.get("characters", [])) or "无"
        beat_lines.append(f"""beat_id: {b['beat_id']}
  type/subtype: {b.get('type','')}/{b.get('subtype','')}
  characters: {chars}
  speaker已知: {b.get('speaker','无')}
  raw_text: {b.get('raw_text','')}
  shot_desc: {b.get('shot_fields', {}).get('prompt', '（待生成）')}
  景别: {b.get('shot_fields', {}).get('shot_size', '未知')}
  情绪: {b.get('shot_fields', {}).get('emotion', '未知')}""")
    beats_block = "\n---\n".join(beat_lines)

    return f"""# 当前segment叙事功能
{segment_desc}

# 原始台词队列（唯一来源，按顺序消耗）
{dialogue_block}

# 需要分配台词的beat列表
{beats_block}

# 任务
按顺序为每个beat判断是否需要台词，需要则从队列里取对应台词，做合理性微调后分配。
台词队列按顺序消耗，每条只用一次。
直接输出JSON数组："""


# ────────────────────────────────────────────────────────────
# LLM 调用
# ────────────────────────────────────────────────────────────

async def _call_llm(system: str, user: str) -> list[dict]:
    """在线程池里跑同步call_llm，避免阻塞事件循环"""
    loop = asyncio.get_event_loop()
    raw  = await loop.run_in_executor(
        None,
        lambda: call_llm(user_prompt=user, system_prompt=system)
    )
    raw = raw.strip()
    raw = re.sub(r'^```(?:json)?\s*', '', raw)
    raw = re.sub(r'\s*```$', '', raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r'\[.*\]', raw, re.DOTALL)
        if m:
            return json.loads(m.group())
        return []


# ────────────────────────────────────────────────────────────
# 单 segment 处理
# ────────────────────────────────────────────────────────────

async def align_segment(
    segment_id:   int,
    segment_desc: str,
    beats:        list[dict],
    dialogues:    list[dict],
    chars:        dict,
) -> dict[str, dict]:
    """
    一次LLM调用处理一个segment的台词分配。
    返回 {beat_id: {has_dialogue, dialogue, speaker, ...}}
    """
    candidate_beats = [
        b for b in beats
        if b.get("characters") and b.get("subtype") != "visual"
    ]

    if not candidate_beats or not dialogues:
        return {
            b["beat_id"]: _none_result(b["beat_id"])
            for b in beats
        }

    system = _build_system(chars)
    user   = _build_user(segment_desc, dialogues, candidate_beats)

    items = await _call_llm(system, user)

    # 建立结果映射
    result_map = {item["beat_id"]: item for item in items if "beat_id" in item}

    # 补全所有beat（包括跳过的空镜beat）
    final = {}
    for b in beats:
        bid = b["beat_id"]
        if bid in result_map:
            final[bid] = result_map[bid]
        else:
            final[bid] = _none_result(bid)

    return final


def _none_result(beat_id: str) -> dict:
    return {
        "beat_id":       beat_id,
        "has_dialogue":  False,
        "dialogue":      "",
        "speaker":       "",
        "dialogue_type": "none",
        "source":        "none",
        "adapt_note":    "",
    }


# ────────────────────────────────────────────────────────────
# 全局并发入口
# ────────────────────────────────────────────────────────────

async def align_all(
    beats:         list[dict],
    raw_dialogues: list[dict],
    chars:         dict,
) -> dict[str, dict]:
    """
    所有segment并发处理台词分配。
    返回 {beat_id: dialogue_info}，存入 session_state["dialogue_state"]
    """
    seg_beats: dict[int, list] = defaultdict(list)
    for b in beats:
        seg_beats[b["segment"]].append(b)

    seg_dialogues = _split_by_segment(raw_dialogues, seg_beats)

    tasks = []
    seg_ids = sorted(seg_beats.keys())
    for seg_id in seg_ids:
        seg_b = seg_beats[seg_id]
        seg_d = seg_dialogues.get(seg_id, [])
        desc  = seg_b[0].get("segment_desc", "") if seg_b else ""
        tasks.append(
            align_segment(
                segment_id   = seg_id,
                segment_desc = desc,
                beats        = seg_b,
                dialogues    = seg_d,
                chars        = chars,
            )
        )

    results = await asyncio.gather(*tasks, return_exceptions=True)

    # 合并结果
    dialogue_state: dict[str, dict] = {}
    for seg_id, res in zip(seg_ids, results):
        if isinstance(res, Exception):
            # 某个segment失败，标记为none
            for b in seg_beats[seg_id]:
                dialogue_state[b["beat_id"]] = _none_result(b["beat_id"])
        else:
            dialogue_state.update(res)

    return dialogue_state


# ────────────────────────────────────────────────────────────
# 统计工具
# ────────────────────────────────────────────────────────────

def dialogue_stats(dialogue_state: dict) -> dict:
    """返回台词分配统计信息"""
    total    = len(dialogue_state)
    has_dia  = sum(1 for v in dialogue_state.values() if v.get("has_dialogue"))
    adapted  = sum(1 for v in dialogue_state.values() if v.get("source") == "adapted")
    original = sum(1 for v in dialogue_state.values() if v.get("source") == "original")

    return {
        "total":    total,
        "has_dialogue": has_dia,
        "original": original,
        "adapted":  adapted,
        "none":     total - has_dia,
        "coverage": f"{has_dia/total*100:.1f}%" if total else "0%",
    }