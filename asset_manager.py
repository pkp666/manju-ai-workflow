"""
asset_manager.py — 人物/场景/道具资产管理

三类资产：
  1. 人物主样貌（base）：基于appearance字段拼提示词，三视图素装
  2. 场景（scene）：基于scene_name+space_desc生成场景参考图
  3. 物品道具（prop）：基于description生成道具参考图

遗传逻辑：
  人物：appearance的可变字段（features/trait/physique/hairstyle/outfit）参与遗传
        固定字段（gender/age_desc/role_en）不变
        evolve_appearance算法：按ratio从历史父代随机取字段
  场景：gene层锁定，expression层进化
  道具：无遗传，直接生图
"""

import json
import re
import random
import threading
from dataclasses import dataclass
from typing import Callable

from llm import call_llm
from script_analyzer import (
    build_char_prompt_base,
    evolve_appearance,
    EVOLVABLE_FIELDS,
)


# ════════════════════════════════════════════════════════════
# 状态初始化
# ════════════════════════════════════════════════════════════

def _init_gacha_state() -> dict:
    """通用抽卡状态"""
    return {
        "status":     "pending",
        "gene":       "",
        "expression": "",
        "prompt":     "",
        "prompts":    ["", "", ""],
        "candidates": [],
        "selected":   None,
        "locked_img": None,
        "generation": 0,
        "history":    [],
    }


def init_char_state() -> dict:
    """
    初始化角色资产。
    appearance字段在Tab0解析后从appearance_init写入。
    """
    return {
        # 台词风格（来自chars_init）
        "speech_style":     "",
        "dialogue_samples": [],

        # 外貌（来自appearance_init，Tab0解析后写入）
        "appearance": {
            # 固定（不参与遗传）
            "gender":   "female",
            "age_desc": "",
            "role_en":  "",
            # 可变（参与遗传）
            "features":  "",
            "trait":     "",
            "physique":  "",
            "hairstyle": "",
            "outfit":    "",
            "makeup":    "",
        },

        # 主样貌抽卡
        "base": _init_gacha_state(),

        # 时期装扮
        "periods": {},
    }


def init_period_state() -> dict:
    return _init_gacha_state()


def init_scene_state() -> dict:
    """初始化场景资产"""
    state = _init_gacha_state()
    state["scene_card"] = {}   # 保留兼容，存space_desc等
    return state


def init_prop_state() -> dict:
    """初始化道具资产"""
    state = _init_gacha_state()
    state["description"] = ""
    state["aliases"]     = []
    return state


def record_round(state: dict, prompts: list, candidates: list) -> dict:
    state["history"].append({
        "generation": state.get("generation", 0),
        "prompts":    list(prompts),
        "candidates": list(candidates),
    })
    return state


# ════════════════════════════════════════════════════════════
# 人物主样貌生图
# ════════════════════════════════════════════════════════════

def generate_base_prompts_3(
    char_name: str,
    char_asset: dict,
    style_suffix: str,
    story_tone: str = "",
) -> tuple[list, str, str]:
    """
    主样貌三视图：素装/无服装，只锁定面部+体态基因。
    outfit不参与base生图（outfit留给时期装扮用）。
    变化部分：3种表情/姿态后缀，由LLM生成。
    返回：(prompts[3], gene, expression)
    """
    appearance = char_asset.get("appearance", {})

    # 主样貌只用面部+体态字段，不加outfit（素装）
    base_appearance = {
        k: v for k, v in appearance.items()
        if k not in ("outfit",)   # 只去掉outfit，保留hairstyle作为面部基因的一部分
    }
    # 加素装说明
    base_suffix = f"plain white undergarment, no costume, character reference sheet, {style_suffix}"
    base_prompt = build_char_prompt_base(char_name, base_appearance, base_suffix)

    # 用LLM生成3种适合该角色的表情/姿态后缀
    variant_system = "你是角色设定师，为角色生成3种不同的表情/姿态描述，用于角色三视图生图。每种各一行，JSON数组输出。"
    variant_prompt = (
        f"角色：{char_name}\n"
        f"性格/台词风格：{char_asset.get('speech_style','')}\n"
        f"标志性特征：{appearance.get('trait','')}\n\n"
        f"生成3种不同的表情/姿态描述（中文，≤20字/条），"
        f"要体现该角色的性格特点。\n"
        f"输出JSON数组：[\"表情姿态1\", \"表情姿态2\", \"表情姿态3\"]"
    )

    try:
        raw      = call_llm(variant_prompt, variant_system)
        variants = _extract_json_array(raw)
        if len(variants) < 3:
            variants = ["威严肃穆，目光俯视", "冷眼静观，神情克制", "眉峰微蹙，若有所思"]
    except Exception:
        variants = ["威严肃穆，目光俯视", "冷眼静观，神情克制", "眉峰微蹙，若有所思"]

    prompts = [
        f"{base_prompt}, {v}"
        for v in variants[:3]
    ]

    # gene = 固定部分（面部+体态，不含表情和outfit）
    gene       = base_prompt
    expression = variants[0] if variants else ""

    return prompts, gene, expression


def optimize_base_prompts_3(
    char_name: str,
    char_asset: dict,
    style_suffix: str,
    user_note: str = "",
    parent_b_appearance: dict = None,
    ratio: float = 0.5,
) -> tuple[list, dict]:
    """
    遗传进化：基于evolve_appearance算法混合外貌字段，重新生图。
    
    参数：
      parent_b_appearance: 历史某代的appearance快照（可变字段）
      ratio: B代基因的混合比例（0.25/0.5/0.75）
    
    返回：(prompts[3], new_appearance)
    """
    current_appearance = char_asset.get("appearance", {})

    if parent_b_appearance and ratio > 0:
        new_appearance = evolve_appearance(
            parent_a=current_appearance,
            parent_b=parent_b_appearance,
            ratio=ratio,
        )
        # 应用用户备注（如果有）
        if user_note:
            # 让LLM根据备注修改对应字段
            note_system = "你是角色设定师，根据用户反馈修改角色外貌的某个字段。只输出需要修改的字段JSON，不修改则输出{}。"
            note_prompt = (
                f"当前外貌字段：{json.dumps(new_appearance, ensure_ascii=False)}\n"
                f"用户反馈：{user_note}\n"
                f"可修改字段：{EVOLVABLE_FIELDS}\n"
                f"输出需要修改的字段，格式：{{\"字段名\": \"新值\"}}"
            )
            try:
                raw     = call_llm(note_prompt, note_system)
                patches = _extract_json_obj(raw)
                for k, v in patches.items():
                    if k in EVOLVABLE_FIELDS and v:
                        new_appearance[k] = v
            except Exception:
                pass
    else:
        new_appearance = dict(current_appearance)

    # 用新appearance重新生成提示词
    temp_asset = dict(char_asset)
    temp_asset["appearance"] = new_appearance
    prompts, gene, expression = generate_base_prompts_3(
        char_name, temp_asset, style_suffix, ""
    )
    return prompts, new_appearance


# ════════════════════════════════════════════════════════════
# 时期装扮生图
# ════════════════════════════════════════════════════════════

def generate_period_prompts_3(
    char_name: str,
    period: str,
    beats: list,
    style_suffix: str,
    story_tone: str = "",
) -> tuple[list, str, str]:
    """
    基于主样貌，覆盖outfit/hairstyle/age_desc生成时期装扮提示词。
    """
    # 从beats找该时期的描述
    descs = []
    for b in beats:
        pm = b.get("period_map") or {}
        if pm.get(char_name) == period:
            descs.append(b.get("raw_text", ""))
    script_descs = "\n".join(descs[:8])

    system = "你是服装设计师，根据剧本描述为角色生成特定时期的服装发型描述。JSON输出。"
    prompt = (
        f"角色：{char_name}，时期：{period}\n"
        f"剧本描述：{script_descs or '无'}\n\n"
        f"为该角色的{period}时期生成3组不同的服装+发型描述（中文，各≤30字/组）。\n"
        f"输出JSON数组：[\n"
        f"  {{\"outfit\": \"服饰描述\", \"hairstyle\": \"发型描述\"}},\n"
        f"  ...\n"
        f"]"
    )

    try:
        raw     = call_llm(prompt, system)
        items   = _extract_json_array(raw)
        if not items:
            items = [
                {"outfit": f"{period}素色布衣", "hairstyle": "简单束发"},
                {"outfit": f"{period}常服",     "hairstyle": "半束发"},
                {"outfit": f"{period}正装",     "hairstyle": "束发带冠"},
            ]
    except Exception:
        items = [{"outfit": f"{period}素色布衣", "hairstyle": "简单束发"}] * 3

    # 找主样貌的appearance作为基础
    # 覆盖outfit和hairstyle
    prompts = []
    for item in items[:3]:
        temp_appearance = {
            "gender":   "male",   # 兜底
            "age_desc": "",
            "role_en":  "",
            "features":  "",
            "trait":     "",
            "physique":  "",
            "hairstyle": item.get("hairstyle", ""),
            "outfit":    item.get("outfit", ""),
            "makeup":    "",
        }
        p = build_char_prompt_base(char_name, temp_appearance, style_suffix)
        prompts.append(p)

    gene       = prompts[0] if prompts else ""
    expression = items[0].get("outfit", "") if items else ""
    return prompts, gene, expression


def optimize_period_prompts_3(
    original_prompt: str,
    style_suffix: str,
    user_note: str = "",
) -> list:
    """时期装扮进化：3个方向（轻微变化）"""
    system = "你是服装设计师，对角色时期装扮提示词进行微变异，生成3个不同方向。"
    prompt = (
        f"原始提示词：{original_prompt}\n"
        f"用户备注：{user_note or '无'}\n\n"
        f"生成3个略有差异的变体提示词（同时期，不同细节），JSON数组输出：\n"
        f"[\"变体1\", \"变体2\", \"变体3\"]"
    )
    try:
        raw    = call_llm(prompt, system)
        items  = _extract_json_array(raw)
        result = [i for i in items if isinstance(i, str)]
    except Exception:
        result = []

    while len(result) < 3:
        result.append(original_prompt)
    return result[:3]


# ════════════════════════════════════════════════════════════
# 场景生图
# ════════════════════════════════════════════════════════════

_SCENE_PROMPT_SYSTEM = """你是场景设定师，根据场景名称和氛围描述生成生图提示词。

输出JSON：
{
  "gene":     "核心场景基因（空间类型+朝代风格，英文，≤30词）",
  "variants": [
    {"expression": "光影变体描述", "full_prompt": "完整英文提示词"},
    {"expression": "光影变体描述", "full_prompt": "完整英文提示词"},
    {"expression": "光影变体描述", "full_prompt": "完整英文提示词"}
  ]
}"""

_SCENE_PROMPT_USER = """场景名：{scene_name}
氛围描述：{space_desc}
主情绪：{dominant_emotion}
故事背景：{story_tone}
风格后缀：{style_suffix}

生成3个不同光影/时段的场景参考图提示词变体。

重要限制：
- 场景图只描述环境、建筑、光影、氛围，绝对不出现任何具名人物
- 不写任何角色名字（如林天雄、林道等）
- 不描写人物表情、眼神、动作细节"""


def generate_scene_prompts_3(
    scene_card: dict,
    style_suffix: str,
    story_tone: str = "",
) -> tuple[list, str, str]:
    """
    基于scene_card（含scene_name/space_desc）生成3个场景提示词。
    scene_card来自Tab0的scenes列表或Tab3的scene_assets[name]["scene_card"]。
    返回：(prompts[3], gene, expression)
    """
    scene_name       = scene_card.get("scene_name") or scene_card.get("name", "")
    space_desc       = scene_card.get("space_desc") or scene_card.get("atmosphere", "")
    dominant_emotion = scene_card.get("dominant_emotion", "")

    prompt = (_SCENE_PROMPT_USER
        .replace("{scene_name}",       scene_name)
        .replace("{space_desc}",       space_desc)
        .replace("{dominant_emotion}", dominant_emotion)
        .replace("{story_tone}",       story_tone or "（未提供）")
        .replace("{style_suffix}",     style_suffix)
    )

    try:
        raw  = call_llm(prompt, _SCENE_PROMPT_SYSTEM)
        data = _extract_json_obj(raw)
    except Exception:
        data = {}

    gene     = data.get("gene", "")
    variants = data.get("variants", [])

    prompts    = []
    expression = ""
    for v in variants:
        if isinstance(v, dict):
            prompts.append(v.get("full_prompt", ""))
            if not expression:
                expression = v.get("expression", "")
        elif isinstance(v, str):
            prompts.append(v)

    while len(prompts) < 3:
        prompts.append(prompts[0] if prompts else f"{scene_name}, {space_desc}, {style_suffix}")

    return prompts[:3], gene, expression


def optimize_scene_prompts_3(
    gene: str,
    original_prompt: str,
    style_suffix: str,
    user_note: str = "",
) -> list:
    """场景遗传进化，gene层不变，expression层变异。"""
    system = "你是场景设定师，基于场景基因进行表现层变异，生成3个不同光影/构图方向。"
    prompt = (
        f"场景基因（不变）：{gene}\n"
        f"当前提示词：{original_prompt}\n"
        f"用户备注：{user_note or '无'}\n\n"
        f"生成3个有明显光影/构图差异的变体，JSON数组：\n"
        f"[\"变体1\", \"变体2\", \"变体3\"]"
    )
    try:
        raw    = call_llm(prompt, system)
        items  = _extract_json_array(raw)
        result = [i for i in items if isinstance(i, str)]
    except Exception:
        result = []

    while len(result) < 3:
        result.append(original_prompt)
    return result[:3]


# ════════════════════════════════════════════════════════════
# 统一进化引擎
# ════════════════════════════════════════════════════════════

def make_history_snapshot(state: dict, snapshot_type: str) -> dict:
    """
    生成history快照条目。
    snapshot_type: "appearance" | "prompt"
    
    统一结构：
    {
      "generation": int,
      "candidates": list,
      "selected":   int | None,
      "snapshot":   dict | str   # appearance dict 或 prompt str
    }
    """
    entry = {
        "generation": state.get("generation", 0),
        "candidates": list(state.get("candidates", [])),
        "selected":   state.get("selected"),
    }
    if snapshot_type == "appearance":
        entry["snapshot"] = dict(state.get("appearance", {}))
    else:
        # prompt：取当前选中的提示词，没选就取第一个
        prompts = state.get("prompts", [])
        sel     = state.get("selected")
        if sel is not None and sel < len(prompts):
            entry["snapshot"] = prompts[sel]
        else:
            entry["snapshot"] = prompts[0] if prompts else ""
    return entry


def evolve_char(
    char_name: str,
    char_asset: dict,
    style_suffix: str,
    parent_b_snapshot: dict,
    ratio: float,
    user_note: str = "",
) -> tuple[list, dict]:
    """
    人物进化：appearance字段混合 → 重新生成提示词。
    parent_b_snapshot = history entry的snapshot（appearance dict）
    返回：(new_prompts[3], new_appearance)
    """
    return optimize_base_prompts_3(
        char_name           = char_name,
        char_asset          = char_asset,
        style_suffix        = style_suffix,
        user_note           = user_note,
        parent_b_appearance = parent_b_snapshot,
        ratio               = ratio,
    )


def evolve_scene(
    gene: str,
    current_prompt: str,
    parent_b_snapshot: str,
    style_suffix: str,
    ratio: float,
    user_note: str = "",
) -> list:
    """
    场景进化：gene层不变，expression层LLM微变异。
    parent_b_snapshot = history entry的snapshot（prompt str）
    ratio转化为文字说明传给LLM。
    返回：new_prompts[3]
    """
    ratio_desc = {
        0.25: "轻微变化，保留大部分当前提示词风格，少量借鉴参考提示词",
        0.50: "均衡混合，融合当前与参考提示词各约一半的特点",
        0.75: "大幅变化，主要借鉴参考提示词风格，少量保留当前提示词特点",
    }.get(ratio, "均衡混合")

    system = "你是场景设定师，基于场景基因进行表现层变异，生成3个不同光影/构图方向。"
    lines = [
        f"场景基因（不变）：{gene}",
        f"当前提示词（父代A）：{current_prompt}",
        f"参考提示词（父代B）：{parent_b_snapshot}",
        f"混合方向：{ratio_desc}",
        f"用户备注：{user_note or '无'}",
        "",
        '生成3个变体，JSON数组：["变体1", "变体2", "变体3"]',
    ]
    prompt = "\n".join(lines)
    try:
        raw    = call_llm(prompt, system)
        items  = _extract_json_array(raw)
        result = [i for i in items if isinstance(i, str)]
    except Exception:
        result = []

    while len(result) < 3:
        result.append(current_prompt)
    return result[:3]


def evolve_prop_or_period(
    current_prompt: str,
    parent_b_snapshot: str,
    style_suffix: str,
    ratio: float,
    user_note: str = "",
) -> list:
    """
    道具/时期装扮进化：整个prompt文本LLM微变异。
    返回：new_prompts[3]
    """
    ratio_desc = {
        0.25: "轻微变化，保留大部分当前提示词",
        0.50: "均衡混合，融合当前与参考各半",
        0.75: "大幅借鉴参考提示词",
    }.get(ratio, "均衡混合")

    system = "你是设定师，对提示词进行变异生成3个变体，JSON数组输出。"
    lines = [
        f"当前提示词（父代A）：{current_prompt}",
        f"参考提示词（父代B）：{parent_b_snapshot}",
        f"混合方向：{ratio_desc}",
        f"用户备注：{user_note or '无'}",
        "",
        '生成3个变体，JSON数组：["变体1", "变体2", "变体3"]',
    ]
    prompt = "\n".join(lines)
    try:
        raw    = call_llm(prompt, system)
        items  = _extract_json_array(raw)
        result = [i for i in items if isinstance(i, str)]
    except Exception:
        result = []

    while len(result) < 3:
        result.append(current_prompt)
    return result[:3]


# ════════════════════════════════════════════════════════════
# 道具生图
# ════════════════════════════════════════════════════════════

_PROP_PROMPT_SYSTEM = "你是道具设定师，根据道具描述生成生图提示词（英文，专注外观细节）。"
_PROP_PROMPT_USER = """道具名：{name}
描述：{description}
风格后缀：{style_suffix}

生成1个道具参考图提示词（英文，突出外观/材质/颜色/细节）。
直接输出提示词文本，不加解释。"""


def generate_prop_prompt(
    name: str,
    description: str,
    style_suffix: str,
    story_tone: str = "",
) -> tuple[list, str, str]:
    """
    生成道具参考图提示词（3个变体）。
    返回：(prompts[3], gene, expression)
    """
    prompt = (_PROP_PROMPT_USER
        .replace("{name}",         name)
        .replace("{description}",  description)
        .replace("{style_suffix}", style_suffix)
    )
    try:
        base_prompt = call_llm(prompt, _PROP_PROMPT_SYSTEM).strip()
    except Exception:
        base_prompt = f"{name}, {description}, {style_suffix}"

    gene = base_prompt
    prompts = [
        f"{base_prompt}, front view",
        f"{base_prompt}, side view, detail",
        f"{base_prompt}, dramatic lighting",
    ]
    return prompts, gene, prompts[0]


def optimize_prompt(
    original_prompt: str,
    style_suffix: str = "",
    user_note: str = "",
) -> list:
    """通用进化（道具/其他），简单变体。"""
    system = "你是设定师，对提示词进行微变异生成3个变体。"
    prompt = (
        f"原始提示词：{original_prompt}\n"
        f"用户备注：{user_note or '无'}\n\n"
        f"生成3个略有差异的变体，JSON数组：[\"变体1\", \"变体2\", \"变体3\"]"
    )
    try:
        raw    = call_llm(prompt, system)
        items  = _extract_json_array(raw)
        result = [i for i in items if isinstance(i, str)]
    except Exception:
        result = []

    while len(result) < 3:
        result.append(original_prompt)
    return result[:3]


# ════════════════════════════════════════════════════════════
# 工具函数（供其他模块使用）
# ════════════════════════════════════════════════════════════

def extract_characters_raw(beats: list) -> list:
    seen, result = set(), []
    for b in beats:
        for c in b.get("characters") or []:
            if c and c not in seen:
                seen.add(c)
                result.append(c)
    return result


def clean_characters(raw_names: list) -> list:
    """LLM清洗人名列表（保持兼容）"""
    if not raw_names:
        return []
    system = "你是人名清洗工具，从列表中去除非角色名（泛称/职位称谓/旁白等），保留具名角色。输出JSON数组。"
    prompt = f"输入人名列表：{raw_names}\n输出清洗后的具名角色列表：[\"角色名1\", \"角色名2\"]"
    try:
        raw    = call_llm(prompt, system)
        result = _extract_json_array(raw)
        return [r for r in result if isinstance(r, str)] or raw_names
    except Exception:
        return raw_names


def extract_scenes(beats: list) -> dict:
    """从beats提取场景（保持兼容，Tab3兜底用）"""
    result = {}
    for b in beats:
        scene = b.get("scene_name") or b.get("scene")
        if scene and scene not in result:
            result[scene] = []
    return result


def extract_periods_from_beats(beats: list, chars: list) -> dict:
    result = {c: set() for c in chars}
    for b in beats:
        pm = b.get("period_map") or {}
        for char, period in pm.items():
            if char in result and period:
                result[char].add(period)
    return {c: sorted(p) if p else ["主线"] for c, p in result.items()}


def extract_props_from_beats(beats: list) -> dict:
    result = {}
    for b in beats:
        for p in b.get("props") or []:
            if p:
                result.setdefault(p, [])
                bid = b.get("beat_id", "")
                if bid and bid not in result[p]:
                    result[p].append(bid)
    return result


def bind_assets(beat: dict, char_assets: dict, scene_assets: dict) -> tuple:
    ref_chars = []
    for name in beat.get("characters") or []:
        if name not in char_assets:
            continue
        base = char_assets[name].get("base", {})
        img  = base.get("locked_img")
        if img:
            ref_chars.append(img)

    scene_name = beat.get("scene_name") or beat.get("scene")
    ref_scene  = None
    if scene_name and scene_name in scene_assets:
        ref_scene = scene_assets[scene_name].get("locked_img")

    return ref_chars, ref_scene


def missing_assets(beat: dict, char_assets: dict, scene_assets: dict) -> list:
    warnings = []
    for name in beat.get("characters") or []:
        if name not in char_assets:
            warnings.append(f"角色「{name}」无资产")
        elif not char_assets[name].get("base", {}).get("locked_img"):
            warnings.append(f"角色「{name}」主样貌未锁定")

    scene_name = beat.get("scene_name") or beat.get("scene")
    if scene_name:
        if scene_name not in scene_assets:
            warnings.append(f"场景「{scene_name}」无资产")
        elif not scene_assets[scene_name].get("locked_img"):
            warnings.append(f"场景「{scene_name}」未锁定")

    return warnings


def build_asset_context(beat: dict, char_assets: dict, scene_assets: dict) -> str:
    lines = []
    for name in beat.get("characters") or []:
        if name not in char_assets:
            continue
        base = char_assets[name].get("base", {})
        img  = base.get("locked_img")
        ap   = char_assets[name].get("appearance", {})
        desc = ", ".join(v for k, v in ap.items()
                        if k in EVOLVABLE_FIELDS and v)
        if desc:
            lines.append(f"人物【{name}】外貌：{desc}")
        if img:
            lines.append(f"  → 已锁定立绘，生图时作为参考图传入")

    scene_name = beat.get("scene_name") or beat.get("scene")
    if scene_name and scene_name in scene_assets:
        sc   = scene_assets[scene_name]
        gene = sc.get("gene", "")
        img  = sc.get("locked_img")
        if gene:
            lines.append(f"场景【{scene_name}】：{gene}")
        if img:
            lines.append(f"  → 已锁定场景图，生图时作为参考图传入")

    return "\n".join(lines) if lines else "（暂无资产）"


def build_prompt_en(beat: dict, fields: dict, style_suffix: str) -> str:
    parts = []
    for k in ["shot_scale", "camera_angle", "subject", "lighting", "mood"]:
        v = fields.get(k, "")
        if v:
            parts.append(v)
    if style_suffix:
        parts.append(style_suffix)
    return ", ".join(p for p in parts if p)


def infer_narrative_phase(segment_id: int, total_segments: int) -> str:
    if total_segments <= 1:
        return "建立(establish)"
    ratio = (segment_id - 1) / max(total_segments - 1, 1)
    if ratio <= 0.15: return "建立(establish) — 交代背景，引入角色和场景"
    if ratio <= 0.40: return "升温(escalate) — 矛盾开始浮现，情绪逐渐积累"
    if ratio <= 0.65: return "高潮(climax) — 核心冲突爆发，情绪最强"
    if ratio <= 0.85: return "转折(turn) — 局势反转或关键决定"
    return "余韵(aftermath) — 情绪收尾，节奏放缓"


def _extract_json_array(text: str) -> list:
    text = text.strip()
    text = re.sub(r'^```[a-z]*\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    text = text.strip()
    s = text.find("[")
    e = text.rfind("]")
    if s == -1 or e == -1:
        return []
    try:
        return json.loads(text[s:e+1])
    except Exception:
        return []


def _extract_json_obj(text: str) -> dict:
    text = text.strip()
    text = re.sub(r'^```[a-z]*\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    text = text.strip()
    s = text.find("{")
    e = text.rfind("}")
    if s == -1 or e == -1:
        return {}
    try:
        return json.loads(text[s:e+1])
    except Exception:
        return {}


# ════════════════════════════════════════════════════════════
# GachaConfig + 批量操作
# ════════════════════════════════════════════════════════════

@dataclass
class GachaConfig:
    """
    一行抽卡的全部配置。
    render_gacha根据snapshot_type自动选择进化逻辑，不需要fn_evolve。

    snapshot_type:
      "appearance" → 人物，进化对象是appearance字段dict
      "prompt"     → 场景/道具/时期，进化对象是prompt文本str
      None         → 不显示进化UI

    evolve_context（人物专用）：
      {"char_name": str, "char_asset": dict}
    场景专用：
      {"gene": str}
    道具/时期：不需要
    """
    key:            str
    label:          str
    state:          dict
    fn_gen_prompts: Callable
    fn_gen_images:  Callable
    suffix:         str  = ""
    img_width:      int  = 300
    snapshot_type:  str  = "prompt"   # "appearance" | "prompt" | None
    evolve_context: dict = None


def batch_gen_prompts(configs: list) -> None:
    need = [c for c in configs if not any(c.state.get("prompts", []))]
    if not need:
        return

    lock = threading.Lock()

    def worker(cfg):
        try:
            result = cfg.fn_gen_prompts()
            if isinstance(result, tuple):
                prompts, gene, expr = result
            else:
                prompts, gene, expr = result, "", ""
            if any(prompts):
                with lock:
                    cfg.state["prompts"]    = list(prompts)
                    cfg.state["prompt"]     = prompts[0]
                    cfg.state["status"]     = "selecting"
                    cfg.state["candidates"] = []
                    cfg.state["selected"]   = None
                    if gene: cfg.state["gene"]       = gene
                    if expr: cfg.state["expression"] = expr
        except Exception as e:
            print(f"[gacha] gen_prompts {cfg.key}: {e}")

    threads = [threading.Thread(target=worker, args=(c,), daemon=True) for c in need]
    for t in threads: t.start()
    for t in threads: t.join()


def batch_gen_images(configs: list, force: bool = False) -> None:
    """
    并发生图：每个config独立调用fn_gen_images。
    force=True时跳过过滤，直接对所有传入的config生图（用于重新生图）。
    """
    if force:
        need = [c for c in configs if any(c.state.get("prompts", []))]
    else:
        need = [c for c in configs
                if any(c.state.get("prompts", []))
                and not any(u for u in c.state.get("candidates", []) if u)]
    if not need:
        return

    lock = threading.Lock()

    def worker(cfg):
        # 每个config独立取自己的prompts，独立生图，独立写回自己的state
        my_prompts = list(cfg.state.get("prompts", []))
        my_key     = cfg.key
        try:
            urls = cfg.fn_gen_images(my_prompts)
            if any(u for u in urls if u):
                with lock:
                    # 严格写回这个config自己的state
                    cfg.state["candidates"] = list(urls)
                    cfg.state["selected"]   = None
        except Exception as e:
            print(f"[gacha] gen_images {my_key}: {e}")

    threads = [threading.Thread(target=worker, args=(c,), daemon=True) for c in need]
    for t in threads: t.start()
    for t in threads: t.join()



# ════════════════════════════════════════════════════════════
# GachaConfig工厂函数（各类型标准配置）
# ════════════════════════════════════════════════════════════

def make_char_config(
    char_name: str,
    char_assets: dict,
    style_suffix: str,
    story_tone: str,
    gen_images_fn: Callable,
) -> "GachaConfig":
    """人物主样貌GachaConfig工厂"""
    return GachaConfig(
        key            = f"base_{char_name}",
        label          = char_name,
        state          = char_assets[char_name]["base"],
        fn_gen_prompts = lambda: generate_base_prompts_3(
            char_name, char_assets[char_name], style_suffix, story_tone),
        fn_gen_images  = gen_images_fn,
        suffix         = style_suffix,
        snapshot_type  = "appearance",
        evolve_context = {"char_name": char_name, "char_asset": char_assets[char_name]},
    )


def make_period_config(
    char_name: str,
    period: str,
    char_assets: dict,
    beats: list,
    style_suffix: str,
    story_tone: str,
    gen_images_fn: Callable,
) -> "GachaConfig":
    """时期装扮GachaConfig工厂"""
    return GachaConfig(
        key            = f"period_{char_name}_{period}",
        label          = f"{char_name} · {period}",
        state          = char_assets[char_name]["periods"][period],
        fn_gen_prompts = lambda: generate_period_prompts_3(
            char_name, period, beats, style_suffix, story_tone),
        fn_gen_images  = gen_images_fn,
        suffix         = style_suffix,
        snapshot_type  = "prompt",
    )


def make_scene_config(
    scene_name: str,
    scene_assets: dict,
    style_suffix: str,
    story_tone: str,
    gen_images_fn: Callable,
) -> "GachaConfig":
    """场景GachaConfig工厂"""
    return GachaConfig(
        key            = f"scene_{scene_name}",
        label          = f"场景：{scene_name}",
        state          = scene_assets[scene_name],
        fn_gen_prompts = lambda: generate_scene_prompts_3(
            scene_assets[scene_name].get("scene_card", {}), style_suffix, story_tone),
        fn_gen_images  = gen_images_fn,
        suffix         = style_suffix,
        snapshot_type  = "prompt",
        evolve_context = {"gene": scene_assets[scene_name].get("gene", "")},
    )


def make_prop_config(
    prop_name: str,
    prop_assets: dict,
    style_suffix: str,
    story_tone: str,
    gen_images_fn: Callable,
) -> "GachaConfig":
    """道具GachaConfig工厂"""
    return GachaConfig(
        key            = f"prop_{prop_name}",
        label          = prop_name,
        state          = prop_assets[prop_name],
        fn_gen_prompts = lambda: generate_prop_prompt(
            prop_name, prop_assets[prop_name].get("description", ""), style_suffix, story_tone),
        fn_gen_images  = gen_images_fn,
        suffix         = style_suffix,
        snapshot_type  = "prompt",
    )

# ════════════════════════════════════════════════════════════
# 兼容旧接口
# ════════════════════════════════════════════════════════════

def init_asset_state(name: str = "") -> dict:
    return init_scene_state()

def init_sub_scene_state() -> dict:
    return _init_gacha_state()

def generate_sub_scene_prompts_3(*args, **kwargs):
    return ["", "", ""], ["", "", ""]

def optimize_sub_scene_prompts_3(original_prompt, style_suffix="", user_note=""):
    return optimize_prompt(original_prompt, style_suffix, user_note)

def extract_char_appearances(*args, **kwargs):
    return []

def extract_scene_infos(*args, **kwargs):
    return []


# ════════════════════════════════════════════════════════════
# 视觉代称生成（Tab3资产锁定后调用一次）
# ════════════════════════════════════════════════════════════

_VISUAL_ALIAS_SYSTEM = """你是一个视觉描述专家。
你的任务是为每个角色生成一个简短的英文视觉代称，用于AI生图的提示词。

规则：
1. 代称必须基于外貌特征，不能包含角色名字
2. 代称必须唯一——每个角色至少有一个特征是其他角色没有的
3. 代称简短（5-10个英文单词），包含最显著的1-2个辨识特征
4. 优先选择：发色/发型、年龄段、体型、面部特征、明显标记（疤痕/胡须等）
5. 输出严格JSON，不加任何解释"""

_VISUAL_ALIAS_USER = """为以下角色生成唯一的英文视觉代称。

角色外貌信息：
{chars_appearance}

输出JSON对象，key是角色名（中文），value是英文视觉代称：
{{
  "角色名1": "distinctive visual description in English",
  "角色名2": "distinctive visual description in English"
}}

要求：
- 每个代称必须能唯一区分该角色（其他角色没有这个特征）
- 不能用角色名字
- 5-10个英文单词
- 只输出JSON，不加解释"""


def generate_visual_aliases(
    chars_init: dict,
    appearance_init: dict,
) -> dict:
    """
    为所有角色生成唯一的英文视觉代称。
    锁定资产后调用一次，存入session_state["visual_aliases"]。

    返回：{"林天雄": "middle-aged man with high topknot and cheek scar", ...}
    """
    import json as _json

    if not appearance_init:
        return {}

    # 构建角色外貌描述
    chars_desc_lines = []
    for char_name, app in appearance_init.items():
        if not isinstance(app, dict):
            continue
        parts = [
            f"gender: {app.get('gender', '')}",
            f"age: {app.get('age_desc', '')}",
            f"features: {app.get('features', '')}",
            f"hairstyle: {app.get('hairstyle', '')}",
            f"physique: {app.get('physique', '')}",
            f"trait: {app.get('trait', '')}",
            f"outfit: {app.get('outfit', '')}",
        ]
        desc = "; ".join(p for p in parts if p.split(": ")[1])
        chars_desc_lines.append(f"{char_name}: {desc}")

    if not chars_desc_lines:
        return {}

    chars_appearance = "\n".join(chars_desc_lines)
    user_prompt = _VISUAL_ALIAS_USER.replace("{chars_appearance}", chars_appearance)

    raw = call_llm(user_prompt, _VISUAL_ALIAS_SYSTEM)

    # 解析JSON
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1]
    if raw.endswith("```"):
        raw = raw.rsplit("\n", 1)[0]
    raw = raw.strip()

    start = raw.find("{")
    end   = raw.rfind("}")
    if start == -1 or end == -1:
        print("[asset_manager] visual_aliases解析失败，返回空")
        return {}

    try:
        aliases = _json.loads(raw[start:end+1])
        # 过滤非字符串值
        return {k: v for k, v in aliases.items() if isinstance(v, str) and v}
    except Exception as e:
        print(f"[asset_manager] visual_aliases JSON解析错误: {e}")
        return {}