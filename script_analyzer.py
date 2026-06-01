# ══════════════════════════════════════════════════════════════
# script_analyzer.py
# Step-1：原文解析模块
#
# 三个独立LLM：
#   LLM1 analyze_chars      → chars_init（台词风格+样本）
#   LLM2 analyze_appearance → appearance_init（外貌字段）
#   LLM3 analyze_props      → props_init（道具描述）
#
# 主入口 analyze_raw_script 并发调用三个LLM
# ══════════════════════════════════════════════════════════════

from __future__ import annotations
import json
import re
import threading
from llm import call_llm


# ── 黑名单 ───────────────────────────────────────────────────

_CHAR_BLACKLIST = {
    "旁白", "VO", "Vo", "vo", "narrator", "narration",
    "众人", "下人", "丫鬟", "小厮", "侍卫", "婆子", "家丁",
    "老太太", "老太君", "族老", "长辈", "众嫂嫂", "嫂嫂们",
}
_CHAR_BLACKLIST_SUFFIX = ("众", "们")
_CHAR_BLACKLIST_PREFIX = ("众", "旁白", "VO", "Vo")


def _is_blacklisted(name: str) -> bool:
    if not name or len(name) > 6:
        return True
    if name in _CHAR_BLACKLIST:
        return True
    if any(name.endswith(p) for p in _CHAR_BLACKLIST_SUFFIX):
        return True
    if any(name.startswith(p) for p in _CHAR_BLACKLIST_PREFIX):
        return True
    return False


def _parse_json(raw: str):
    raw = raw.strip()
    raw = re.sub(r'^```(?:json)?\s*', '', raw)
    raw = re.sub(r'\s*```$', '', raw)
    raw = raw.strip()
    try:
        return json.loads(raw)
    except Exception:
        for start_char, end_char in [('{', '}'), ('[', ']')]:
            s = raw.find(start_char)
            e = raw.rfind(end_char)
            if s != -1 and e != -1:
                try:
                    return json.loads(raw[s:e+1])
                except Exception:
                    pass
    return None


# ════════════════════════════════════════════════════════════
# LLM1：角色台词性格提取
# ════════════════════════════════════════════════════════════

_CHARS_SYSTEM = """你是漫剧剧本分析师，从原文提取所有具名角色的台词风格信息。

# 台词风格归纳标准（分级）

【一级：台词>=3句】
  直接从台词样本归纳：
  - 句式特点（长句/短句/反问/命令/陈述）
  - 自称/称谓习惯（本宫/我/名字/谦称）
  - 情绪表达方式（克制/外露/讽刺/温柔）
  - 标志性用词或口头禅
  - 台词节奏（急促/平缓/停顿多）

【二级：台词1-2句】
  结合台词样本+行为描写+他人评价+身份推断
  speech_style末尾标注（推断）

【三级：完全无台词】
  基于身份/关系/情绪推断
  speech_style末尾标注（推断，无台词样本）

# 禁止项
- 禁止出现：旁白/VO/众人/丫鬟/小厮/侍卫/婆子
- 禁止写待补充，每个角色必须给出具体描述
- dialogue_samples只收录原文出现的台词，不创造"""

_CHARS_USER = """# 故事背景
{story_tone}

# 原始剧本
{raw_script}

# 任务
提取所有具名角色的台词风格信息。

输出严格JSON对象，不加任何解释：
{{
  "角色名": {{
    "role":             "身份定位（2-4字）",
    "speech_style":     "台词风格（1-2句，具体可执行）",
    "dialogue_samples": ["原文台词1", "原文台词2"],
    "dialogue_count":   0
  }}
}}"""


def analyze_chars(raw_script: str, story_tone: str) -> dict:
    """LLM1：提取所有角色的台词风格。返回 chars_init 字典。"""
    user = _CHARS_USER.format(
        story_tone=story_tone or "（未提供）",
        raw_script=raw_script,
    )
    raw  = call_llm(user, _CHARS_SYSTEM)
    data = _parse_json(raw)
    if not isinstance(data, dict):
        return {}

    chars_init = {}
    for name, info in data.items():
        if not isinstance(info, dict):
            continue
        if _is_blacklisted(name):
            continue
        chars_init[name] = {
            "role":             info.get("role", ""),
            "speech_style":     info.get("speech_style", ""),
            "dialogue_samples": info.get("dialogue_samples", []),
            "dialogue_count":   info.get("dialogue_count", 0),
        }
    return chars_init


# ════════════════════════════════════════════════════════════
# LLM2：角色外貌提取
# ════════════════════════════════════════════════════════════

_APPEARANCE_SYSTEM = """你是影视角色设定师，从原文提取每个角色的外貌信息。
原文没有描写时，结合朝代/身份/年龄合理推断，标注（推断）。
每个字段控制在20字内，不填人名。

# age_desc字段（AI生图用的英文）
  少年（10-18岁）→ young teenager, around 15
  青年（18-30岁）→ young adult, around 25
  中年（30-50岁）→ middle-aged, around 40
  老年（50岁以上）→ elderly, around 60

# role_en字段（AI生图用的英文身份）
  结合性别+朝代+身份写，例：
  镇国公太夫人 → imperial dowager, noble matriarch, ancient chinese
  少将军       → young general, ancient chinese warrior
  丫鬟         → young maidservant, ancient chinese

# 可变外貌字段（中文）
features   五官特点（眼/眉/唇标志性特征，≤20字）
trait      标志性外貌细节（最突出一两个特征，≤15字）
physique   体态（身高/身材/站姿，≤15字）
hairstyle  发型（发色+款式+冠饰，≤20字）
outfit     服饰（款式+颜色+材质+腰带，≤30字）
makeup     妆容（仅女性，≤15字）"""

_APPEARANCE_USER = """# 故事背景
{story_tone}

# 原始剧本
{raw_script}

# 需要提取外貌的角色列表
{char_names}

# 任务
为每个角色提取外貌信息，原文有则用原文，原文没有则推断并标注。

输出严格JSON对象：
{{
  "角色名": {{
    "gender":    "male或female",
    "age_desc":  "elderly woman, around 60",
    "role_en":   "imperial dowager, noble matriarch, ancient chinese",
    "features":  "凤眼细长，眼神锐利",
    "trait":     "拄拐杖，眉心常蹙",
    "physique":  "清瘦挺直，背脊如松",
    "hairstyle": "银发束髻，黑玉冠",
    "outfit":    "黑底金纹广袖长袍，玉腰带",
    "makeup":    "淡妆素雅（仅女性填写，男性留空字符串）"
  }}
}}"""


def analyze_appearance(
    raw_script: str,
    story_tone: str,
    char_names: list,
) -> dict:
    """LLM2：提取每个角色的外貌字段。返回 appearance_init 字典。"""
    if not char_names:
        return {}

    user = _APPEARANCE_USER.format(
        story_tone=story_tone or "（未提供）",
        raw_script=raw_script,
        char_names="、".join(char_names),
    )
    raw  = call_llm(user, _APPEARANCE_SYSTEM)
    data = _parse_json(raw)
    if not isinstance(data, dict):
        return {}

    appearance_init = {}
    for name, info in data.items():
        if not isinstance(info, dict):
            continue
        if _is_blacklisted(name):
            continue
        appearance_init[name] = {
            # 固定（不参与遗传）
            "gender":   info.get("gender", "male"),
            "age_desc": info.get("age_desc", ""),
            "role_en":  info.get("role_en", ""),
            # 可变（参与遗传）
            "features":  info.get("features", ""),
            "trait":     info.get("trait", ""),
            "physique":  info.get("physique", ""),
            "hairstyle": info.get("hairstyle", ""),
            "outfit":    info.get("outfit", ""),
            "makeup":    info.get("makeup", ""),
        }
    return appearance_init


# ════════════════════════════════════════════════════════════
# LLM3：道具提取
# ════════════════════════════════════════════════════════════

_PROPS_SYSTEM = """你是影视道具分析师，从剧本原文推导所有重要物品。

# 提取标准
有名字 + 有具体外观 + 在剧情中有实际作用
包括：武器/道具/装备/信物/法宝/特殊器物
物品可能有多种代称，要囊括所有代称。

# 描述要求
外观+材质+颜色+尺寸+特殊标记，不含人名，≤50字

# 排除项
普通家具/祭祀用品/建筑构件/随身配饰（帕子/发簪等）/氛围描述"""

_PROPS_USER = """# 原始剧本
{raw_script}

# 任务
提取所有重要物品道具。没有符合条件的则输出空数组。

输出严格JSON数组：
[
  {{
    "name":        "道具名称（2-6字）",
    "aliases":     ["代称1", "代称2"],
    "description": "外观+材质+颜色+尺寸+特殊标记（不含人名，<=50字）"
  }}
]"""


def analyze_props(raw_script: str) -> list:
    """LLM3：提取道具列表。返回 props_init 列表。"""
    user = _PROPS_USER.format(raw_script=raw_script)
    raw  = call_llm(user, _PROPS_SYSTEM)
    data = _parse_json(raw)
    if not isinstance(data, list):
        return []
    return [
        {
            "name":        p.get("name", ""),
            "aliases":     p.get("aliases", []),
            "description": p.get("description", ""),
        }
        for p in data
        if isinstance(p, dict) and p.get("name")
    ]


# ════════════════════════════════════════════════════════════
# 主入口
# ════════════════════════════════════════════════════════════

def analyze_raw_script(
    raw_script: str,
    story_tone: str = "",
) -> dict:
    """
    并发调用三个LLM提取角色/外貌/道具。

    流程：
      第一批并发：LLM1（角色）+ LLM3（道具）
      第二批串行：LLM2（外貌，依赖LLM1的角色名）

    返回：
    {
      "chars_init":      {角色名: {role, speech_style, dialogue_samples, dialogue_count}},
      "appearance_init": {角色名: {gender, age_desc, role_en, features, ...}},
      "props_init":      [{name, aliases, description}],
      "char_names":      [角色名列表],
    }
    """
    results = {}
    lock    = threading.Lock()
    errors  = {}

    def run_chars():
        try:
            r = analyze_chars(raw_script, story_tone)
            with lock:
                results["chars_init"] = r
        except Exception as e:
            with lock:
                errors["chars"] = str(e)
                results["chars_init"] = {}

    def run_props():
        try:
            r = analyze_props(raw_script)
            with lock:
                results["props_init"] = r
        except Exception as e:
            with lock:
                errors["props"] = str(e)
                results["props_init"] = []

    # 第一批：chars和props并发
    t_chars = threading.Thread(target=run_chars, daemon=True)
    t_props = threading.Thread(target=run_props, daemon=True)
    t_chars.start()
    t_props.start()
    t_chars.join()
    t_props.join()

    # 第二批：appearance依赖chars_init的角色名
    char_names = list(results.get("chars_init", {}).keys())
    try:
        appearance_init = analyze_appearance(raw_script, story_tone, char_names)
    except Exception as e:
        errors["appearance"] = str(e)
        appearance_init = {}

    results["appearance_init"] = appearance_init

    if errors:
        print(f"[analyzer] 部分LLM失败：{errors}")

    return {
        "chars_init":      results.get("chars_init", {}),
        "appearance_init": results.get("appearance_init", {}),
        "props_init":      results.get("props_init", []),
        "char_names":      char_names,
    }


# ════════════════════════════════════════════════════════════
# 工具函数
# ════════════════════════════════════════════════════════════

def format_chars_for_prompt(chars_init: dict) -> str:
    """把chars_init格式化为可注入提示词的文本块。"""
    if not chars_init:
        return "（暂无人物档案）"

    SKIP = {"待补充", ""}
    lines = []
    for name, info in chars_init.items():
        role         = info.get("role", "")
        speech_style = info.get("speech_style", "")
        samples      = info.get("dialogue_samples", [])

        lines.append(f"  {name}（{role}）")
        lines.append(f"    台词风格：{speech_style if speech_style not in SKIP else '未知'}")
        if samples:
            lines.append(f"    原文台词样本：")
            for s in samples[:3]:
                lines.append(f"      「{s}」")

    return "\n".join(lines)


# ════════════════════════════════════════════════════════════
# 生图提示词构建（Tab3使用）
# ════════════════════════════════════════════════════════════

EVOLVABLE_FIELDS = ["features", "trait", "physique", "hairstyle", "outfit"]


def build_char_prompt_base(
    char_name: str,
    appearance: dict,
    style_suffix: str = "",
) -> str:
    """
    基于appearance字段拼接角色生图基础提示词。
    固定部分（role_en + age_desc）+ 可变部分。
    """
    age_desc = appearance.get("age_desc", "")
    role_en  = appearance.get("role_en", "")
    gender   = appearance.get("gender", "female")

    fixed = ", ".join(p for p in [role_en, age_desc] if p)

    fields = []
    for key in ["features", "trait", "physique", "hairstyle", "outfit"]:
        v = appearance.get(key, "")
        if v:
            fields.append(v)
    if gender == "female" and appearance.get("makeup"):
        fields.insert(3, appearance["makeup"])

    variable = "，".join(fields)

    prefix = (
        "character design sheet, UE5 rendering style, "
        "high quality CG animation character, "
        "left side bust portrait, right side front/side/back three views, "
        "50% grey background, "
        f"character name '{char_name}' in top left corner, "
    )

    parts = [p for p in [prefix, fixed, variable, style_suffix] if p]
    return ", ".join(parts)


def evolve_appearance(
    parent_a: dict,
    parent_b: dict,
    ratio: float,
) -> dict:
    """
    遗传进化：按ratio从parent_b随机取字段替换parent_a。

    ratio = B的比例：
      0.25 → 25%字段取B，75%取A（轻微变异）
      0.50 → 各50%随机混合
      0.75 → 75%字段取B（大幅变异）

    只有EVOLVABLE_FIELDS参与遗传，固定字段不变。
    """
    import random
    child = dict(parent_a)
    for field in EVOLVABLE_FIELDS:
        if random.random() < ratio:
            if parent_b.get(field):
                child[field] = parent_b[field]
    return child