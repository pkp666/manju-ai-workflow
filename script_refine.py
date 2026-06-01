"""
script_refine.py — 第零步：剧本精炼

完整流程：
  Step-A：论调问答（是/否）→ story_tone
  Step-B：叙事结构分析    → segment_structure
  Step-C：精炼问答        → refine_qa（基于叙事结构空白点）
  Step-D：叙事结构切分    → segments（含original_text）
  Step-E：漫剧化改编      → drama_segments（含drama_text）
  Step-F：场景细分        → scenes（含scene_text）

公开接口：
  # Step-A
  next_tone_question(raw, qa_pairs) -> dict | None
  build_tone(raw, qa_pairs) -> str
  patch_tone(tone, user_feedback) -> str

  # Step-B
  analyze_segment_structure(raw_script, tone, chars_init) -> dict

  # Step-C
  next_refine_question(raw, tone, qa_pairs, segment_structure) -> dict | None
  refresh_options(raw, tone, question, old_options, qa_pairs) -> list[str]

  # Step-D
  split_by_segments(raw_script, tone, qa_pairs, segment_structure) -> list[dict]

  # Step-E
  dramatize_all_segments(segments, tone, chars_init, qa_pairs, segment_structure) -> list[dict]
  dramatize_single_segment(segment, tone, chars_init, qa_pairs, prev_tail, segment_structure) -> dict

  # Step-F
  split_all_scenes(drama_segments) -> list[dict]
  split_scenes_from_segment(drama_segment) -> list[dict]
"""

import json
import threading
import re
from llm import call_llm


# ════════════════════════════════════════════════════════════════
# Step-A — 论调问答（是/否）
# ════════════════════════════════════════════════════════════════

_TB_QUESTION_SYSTEM = """你是资深故事顾问，通过是/否问答帮助用户确立故事基本性质。

规则：
- 每次只问一个问题，是/否格式
- 问题必须联系剧本原文的具体情节或人物
- 覆盖新维度，不重复已问过的
- 从宏观到具体：先问类型定位，再问叙事方向
- 已有足够信息时输出 CONVERGED"""

_TB_QUESTION_USER = """剧本原文：
{raw_script}

已确认的问答：
{qa_history}

请提出下一个是/否问题。
已有足够信息则输出：CONVERGED

否则输出严格JSON：
{{
  "question": "问题（联系原文具体情节，一句话）",
  "yes_means": "如果是，意味着什么",
  "no_means": "如果否，意味着什么"
}}"""

_TB_BUILD_SYSTEM = """你是资深故事顾问，根据是/否问答总结故事核心论调。

论调说明：
- 这是什么类型的故事，面向什么读者
- 核心叙事方向和主题
- 主要人物关系性质
- 整体叙事基调
写得具体准确，不用空洞形容词。"""

_TB_BUILD_USER = """剧本原文：
{raw_script}

用户确认的问答：
{qa_history}

请生成故事论调。直接输出一段话。"""

_TB_PATCH_SYSTEM = """根据用户修改意见调整故事论调。直接输出修改后的论调。"""

_TB_PATCH_USER = """当前论调：
{tone}

用户修改意见：
{user_feedback}

请输出修改后的论调。"""


def next_tone_question(raw_script: str, qa_pairs: list[dict]) -> dict | None:
    qa_history = _build_yesno_history(qa_pairs)
    prompt = (_TB_QUESTION_USER
        .replace("{raw_script}", raw_script)
        .replace("{qa_history}", qa_history)
    )
    raw = call_llm(prompt, _TB_QUESTION_SYSTEM).strip()
    if "CONVERGED" in raw:
        return None
    return _parse_tone_question(raw)


def build_tone(raw_script: str, qa_pairs: list[dict]) -> str:
    qa_history = _build_yesno_history(qa_pairs)
    prompt = (_TB_BUILD_USER
        .replace("{raw_script}", raw_script)
        .replace("{qa_history}", qa_history)
    )
    return call_llm(prompt, _TB_BUILD_SYSTEM)


def patch_tone(tone: str, user_feedback: str) -> str:
    prompt = (_TB_PATCH_USER
        .replace("{tone}", tone)
        .replace("{user_feedback}", user_feedback)
    )
    return call_llm(prompt, _TB_PATCH_SYSTEM)


# ════════════════════════════════════════════════════════════════
# Step-B — 叙事结构分析
# ════════════════════════════════════════════════════════════════

_NARRATIVE_STRUCTURE_SYSTEM = """你是拥有20年经验的漫剧编剧，同时精通影视叙事结构理论。

你的任务是分析原始剧本的叙事结构，为后续漫剧化改编提供精准的结构骨架。

# 分析思维

第一步：判断集数类型
  首集：需要建立世界观、人物、核心矛盾，前3秒必须有钩子
  续集：需要快速承接上集悬念，推进主线冲突
  完结集：需要解决核心矛盾，给出情感落点或新悬念

第二步：识别叙事节拍
  每个叙事模块 = 一个完整的戏剧节拍
  节拍有明确的开始状态、触发事件、结束状态
  节拍之间有因果关系，不是简单的时间顺序

第三步：定位叙事空白点
  空白点 = 原文中信息模糊或有多种解读可能的地方
  空白点的选择会影响整集的情绪走向和人物动机
  必须引用原文具体词句，不能抽象描述

# 模块命名原则
  用具体剧情命名，不用通用术语
  错误：「矛盾冲突段」「信息铺垫段」
  正确：「灵堂林尘被叫醒」「秦赛花宣布一肩挑八房」

# 空白点质量标准
  必须是真正有歧义的地方，不是显而易见的事实
  用户的选择必须影响后续改编的具体内容
  每个空白点对应一个可以问的问题"""

_NARRATIVE_STRUCTURE_USER = """【故事论调（定性基础）】
{tone}

【已知人物档案】
{chars_block}

【原始剧本】
{raw_script}

请完成以下分析，输出严格JSON，不加任何解释：
{{
  "episode_type": "首集/续集/完结集",
  "core_conflict": "这集的核心矛盾是什么（一句话，引用原文人名和事件）",
  "emotional_core": "这集的情感内核是什么（观众应该感受到什么）",
  "structure_desc": "整体叙事逻辑（一句话，说清楚事件推进的因果链）",
  "modules": [
    {{
      "segment_id": 1,
      "segment_name": "模块名称（4-8字，用具体剧情命名）",
      "segment_function": "这个节拍的叙事功能（一句话：从什么状态，发生了什么，到什么状态）",
      "start_state": "本模块开始时的人物/情境状态",
      "end_state": "本模块结束时的人物/情境状态",
      "key_event": "本模块的关键事件（引用原文）",
      "narrative_gaps": [
        "空白点（格式：【原文词句】→ 这里有歧义：A解读意味着...，B解读意味着...）"
      ],
      "hook_type": "opening_hook/ending_suspense/null"
    }}
  ]
}}

要求：
  模块数量由剧情决定，通常3-8个，不强求固定数量
  每个模块的narrative_gaps至少1个，最多3个
  hook_type只有第一个模块可以是opening_hook，最后一个模块可以是ending_suspense"""


def analyze_segment_structure(
    raw_script: str,
    tone: str,
    chars_init: dict | None = None,
) -> dict:
    """
    Step-B：叙事结构分析
    输出叙事结构，供精炼问答和切分使用
    """
    from script_analyzer import format_chars_for_prompt
    chars_block = format_chars_for_prompt(chars_init or {})

    user = (_NARRATIVE_STRUCTURE_USER
        .replace("{tone}",        tone)
        .replace("{chars_block}", chars_block)
        .replace("{raw_script}",  raw_script)
    )

    raw = call_llm(user, _NARRATIVE_STRUCTURE_SYSTEM)
    raw = _strip_json(raw)

    try:
        result = json.loads(raw)
        # 确保关键字段存在
        result.setdefault("episode_type",   "首集")
        result.setdefault("core_conflict",  "")
        result.setdefault("emotional_core", "")
        result.setdefault("structure_desc", "")
        result.setdefault("modules",        [])
        return result
    except Exception:
        return {
            "episode_type":   "首集",
            "core_conflict":  "",
            "emotional_core": "",
            "structure_desc": "",
            "modules":        [],
        }


# ════════════════════════════════════════════════════════════════
# Step-C — 精炼问答（基于叙事结构空白点）
# ════════════════════════════════════════════════════════════════

_SR_QUESTION_SYSTEM = """你是漫剧叙事顾问，针对叙事结构的空白点提问，帮助用户确认关键叙事细节。

# 提问原则

提问目的：
  让用户确认原文中有歧义的叙事细节
  用户的选择直接影响后续漫剧化改编的人物动机、情绪走向、台词风格

问题质量标准：
  必须是原文中真实存在的歧义，不是凭空假设
  问题必须引用原文的具体词句或事件
  选项之间必须有实质性叙事差异（不同的因果关系，不同的人物动机）
  
  好问题示例：
    「秦赛花说"做出了决定"时，林尘"以为自己要被逐出家族"——这个误判是否是秦赛花故意制造的？」
  
  坏问题示例：
    「这个故事的情绪基调是什么？」（太抽象，不是叙事事实）
    「林尘是好人还是坏人？」（显而易见）

选项格式：
  [具体叙事结论] ——「原文词句作为依据」→ [这个选择对后续改编的影响]

# 收敛标准
  所有模块的核心空白点都已确认 → 输出 CONVERGED
  用户已跳过/完成问答 → 输出 CONVERGED"""

_SR_QUESTION_USER = """【故事论调】
{tone}

【叙事结构分析】
核心矛盾：{core_conflict}
情感内核：{emotional_core}
整体逻辑：{structure_desc}

各模块叙事空白点：
{narrative_gaps}

【剧本原文】
{raw_script}

【已确认的叙事细节（不重复这些问题）】
{qa_history}

请从叙事空白点中选取当前最关键的一个提问。
所有关键空白点已确认则输出：CONVERGED

否则输出严格JSON：
{{
  "question": "问题（必须引用原文词句，一句话）",
  "which_module": "这个问题属于哪个叙事模块",
  "options": [
    "选项A结论 ——「原文词句」→ 改编影响",
    "选项B结论 ——「原文词句」→ 改编影响",
    "选项C结论 ——「原文词句」→ 改编影响"
  ]
}}"""

_SR_REFRESH_SYSTEM = """重新生成3个不同方向的选项，与旧选项方向完全不同。
格式：[具体叙事结论] ——「原文词句」→ [对改编的影响]
选项之间必须有实质性叙事差异。"""

_SR_REFRESH_USER = """【故事论调】{tone}
【剧本原文】{raw_script}
【已确认问答】{qa_history}
问题：{question}
旧选项（新选项必须与这些方向完全不同）：
{old_options}

输出严格JSON数组（3个字符串）：
["新选项A", "新选项B", "新选项C"]"""


def next_refine_question(
    raw_script: str,
    tone: str,
    qa_pairs: list[dict],
    segment_structure: dict | None = None,
) -> dict | None:
    """Step-C：推导下一个叙事问题"""
    qa_history = _build_refine_history(qa_pairs)

    if segment_structure and segment_structure.get("modules"):
        all_gaps = []
        for m in segment_structure.get("modules", []):
            for g in m.get("narrative_gaps", []):
                all_gaps.append(f"[{m['segment_name']}] {g}")
        gaps_text = "\n".join(f"- {g}" for g in all_gaps)

        prompt = (_SR_QUESTION_USER
            .replace("{tone}",           tone)
            .replace("{core_conflict}",  segment_structure.get("core_conflict", ""))
            .replace("{emotional_core}", segment_structure.get("emotional_core", ""))
            .replace("{structure_desc}", segment_structure.get("structure_desc", ""))
            .replace("{narrative_gaps}", gaps_text)
            .replace("{raw_script}",     raw_script)
            .replace("{qa_history}",     qa_history)
        )
    else:
        prompt = (
            f"【故事论调】\n{tone}\n\n"
            f"【剧本原文】\n{raw_script}\n\n"
            f"【已确认问答】\n{qa_history}\n\n"
            "请找出最关键的叙事歧义，提出一个问题和3个选项。\n"
            "已足够完整则输出：CONVERGED\n\n"
            '否则输出JSON：{"question": "...", "options": ["A", "B", "C"]}'
        )

    raw = call_llm(prompt, _SR_QUESTION_SYSTEM).strip()
    if "CONVERGED" in raw:
        return None
    return _parse_refine_question(raw)


def refresh_options(
    raw_script: str,
    tone: str,
    question: str,
    old_options: list[str],
    qa_pairs: list[dict] | None = None,
) -> list[str]:
    qa_history = _build_refine_history(qa_pairs or [])
    prompt = (_SR_REFRESH_USER
        .replace("{tone}",        tone)
        .replace("{raw_script}",  raw_script)
        .replace("{qa_history}",  qa_history)
        .replace("{question}",    question)
        .replace("{old_options}", "\n".join(f"- {o}" for o in old_options))
    )
    raw = call_llm(prompt, _SR_REFRESH_SYSTEM)
    return _parse_options(raw, fallback=old_options)


# ════════════════════════════════════════════════════════════════
# Step-D — 按叙事结构切分原文 → segments
# ════════════════════════════════════════════════════════════════

_MODULE_SPLIT_SYSTEM = """你是专业漫剧编剧，根据叙事结构对原文进行精确切分。

# 切分原则

铁律（绝对不违反）：
  原文内容零损失：台词、动作、OS、旁白全部原样保留
  所有original_text首尾相接必须等于完整原文

切分依据：
  严格按照叙事结构的模块划分切分
  每个模块对应一段叙事节拍（有明确的开始状态和结束状态）
  用户确认的叙事细节是高权重参考，影响模块边界的判断

边界判断：
  模块边界在叙事节拍的自然落点
  不在台词中间切断
  不在同一事件的反应链中间切断

# 输出要求
  segment_id与叙事结构保持一致
  original_text必须是原文的原样复制，不增删改"""

_MODULE_SPLIT_USER = """【故事论调】
{tone}

【叙事结构（切分依据）】
核心矛盾：{core_conflict}
整体逻辑：{structure_desc}
模块定义：
{structure_json}

【用户确认的叙事细节（高权重，影响边界判断）】
{confirmed_qa}

【原始剧本（完整原文）】
{raw_script}

请严格按照叙事结构切分原文，输出严格JSON数组：
[{{
  "segment_id": 1,
  "segment_name": "与叙事结构一致的模块名称",
  "segment_function": "与叙事结构一致的叙事功能",
  "original_text": "原文片段，原样复制，一字不改，不增不删",
  "hook_type": "opening_hook/ending_suspense/null"
}}]

验证：把所有original_text连接起来，必须与原文完全一致。"""


def split_by_segments(
    raw_script: str,
    tone: str,
    qa_pairs: list[dict],
    segment_structure: dict,
) -> list[dict]:
    """
    Step-D：按叙事结构切分原文
    输出segments列表，每个segment含original_text
    """
    confirmed_qa   = _build_confirmed_qa(qa_pairs)
    structure_json = json.dumps(
        segment_structure.get("modules", []),
        ensure_ascii=False, indent=2
    )

    user = (_MODULE_SPLIT_USER
        .replace("{tone}",           tone)
        .replace("{core_conflict}",  segment_structure.get("core_conflict", ""))
        .replace("{structure_desc}", segment_structure.get("structure_desc", ""))
        .replace("{structure_json}", structure_json)
        .replace("{confirmed_qa}",   confirmed_qa)
        .replace("{raw_script}",     raw_script)
    )

    raw = call_llm(user, _MODULE_SPLIT_SYSTEM)
    raw = _strip_json(raw)

    start = raw.find("[")
    end   = raw.rfind("]")
    if start == -1 or end == -1:
        return _fallback_single_segment(raw_script, segment_structure)

    try:
        modules = json.loads(raw[start:end+1])
        valid = [
            m for m in modules
            if isinstance(m, dict) and m.get("original_text", "").strip()
        ]
        return valid if valid else _fallback_single_segment(raw_script, segment_structure)
    except Exception:
        return _fallback_single_segment(raw_script, segment_structure)


def _fallback_single_segment(raw_script: str, segment_structure: dict) -> list[dict]:
    return [{
        "segment_id":      1,
        "segment_name":    "完整剧本",
        "segment_function": segment_structure.get("structure_desc", ""),
        "original_text":   raw_script,
        "hook_type":       "opening_hook",
    }]


# ════════════════════════════════════════════════════════════════
# Step-E — 逐segment漫剧化改编（并发）
# ════════════════════════════════════════════════════════════════

_DRAMATIZE_SYSTEM = """你是拥有20年经验的漫剧编剧，把原文叙事模块改编成漫剧脚本文本。
同时输出 drama_text 和 dialogue_structure 两个部分。

# 改编本质
不是扩写，是改写：把小说/大纲语言转化为漫剧脚本语言
保留所有原文事件和结果，改变的是表达方式
宁可精简，不可堆砌

# 第一步：根据原文字数确定宫格目标和台词上限（必须先做）

原文字数决定drama_text的规模：
  原文≤60字   → 目标4格（2×2），台词节点≤3条，drama_text≤120字
  原文61-150字 → 目标9格（3×3），台词节点≤6条，drama_text≤250字
  原文151-350字→ 目标9-16格，   台词节点≤10条，drama_text≤400字
  原文>350字   → 目标16-25格，  台词节点≤16条，drama_text≤原文1.2倍

台词节点数 = dialogue_structure的条数上限，严格不超过。
宁可合并相似节点，不可超出上限。

# 第二步：台词结构规划

在上限内规划台词骨架：
  约70%节点有台词，30%是纯动作/场景
  纯动作节点≤2个（用于转折/爆发）
  空景节点≤1个（用于场景交代）
  一个人连续说多句 → 合并为1条
  多人同时反应（如七人齐惊）→ 合并为1条sfx或选最有代表性的1人说

规划每条台词：
  speaker     谁说
  type        dialogue/inner/narration/sfx
  trigger     触发节点（引用原文事件）
  style_note  结合personality+speech_style+当前情绪，例：
              "秦赛花发现林尘失态，愤怒压抑，短促命令式，≤15字"

# 第三步：改编（严格按dialogue_structure执行）

## 写法铁律：动作和台词写在同一段落，不分行

正确写法：
  秦赛花拄杖上前，冷声道："成何体统。"
  林尘猛地坐直，椅子摩擦出刺耳声。

错误写法：
  秦赛花拄杖上前，目光如刀。
  秦赛花："成何体统。"
  （动作和台词不要分成两行）

每个台词节点 = 一段：动作描写（≤15字）+ 台词（≤20字）合并成一句话
纯动作节点 = 一句动作描写
旁白 = 一句旁白

## 步骤一：台词标准化
  ① 有完整台词 → 保留语义，按style_note风格改写，≤20字
  ② 有说话动作但台词模糊 → 按style_note还原，标注（还原）
  ③ 有具名人物无台词 → 按style_note补充，标注（补）
  ④ sfx节点 → 只写动作，不补台词
  ⑤ 空镜/无具名人物 → 不补台词

  台词格式：
    对白：角色名："台词"
    内心：角色名（内心）："内容"
    旁白："内容"

## 步骤二：场景补充
  段落开头一句话交代光影和空间感，之后不重复

## 步骤三：节奏控制
  连续旁白≤2句
  情绪高点放大，不平铺直叙

# 特殊规则（优先级最高）
首段（opening_hook）：第1句强反差钩子，禁止平淡开场
尾段（ending_suspense）：最后2句留悬念，禁止情绪完全释放

# 输出格式（必须严格遵守）
输出两个部分，用分隔线隔开：

===DRAMA_TEXT===
（改编后的漫剧脚本文本，直接输出正文）

===DIALOGUE_STRUCTURE===
（JSON数组，台词结构规划）
[
  {
    "order": 1,
    "speaker": "角色名或旁白",
    "type": "dialogue/inner/narration/sfx",
    "trigger": "触发这句话的叙事节点（引用原文）",
    "style_note": "结合性格+当前情绪的台词风格备注（具体可执行）"
  }
]"""

_DRAMATIZE_USER = """【故事论调】
{tone}

【叙事结构整体逻辑】
核心矛盾：{core_conflict}
情感内核：{emotional_core}
整体逻辑：{structure_desc}

【用户确认的叙事细节（高权重，改编时体现）】
{confirmed_qa}

【人物档案（性格+台词风格，台词规划必须参考）】
{chars_block}

【各角色台词改写参考】
{dialogue_style_hint}

【本segment信息】
segment名称：{segment_name}
叙事功能：{segment_function}
开始状态：{start_state}
结束状态：{end_state}
关键事件：{key_event}
特殊规则：{hook_type_desc}

【上一segment结尾（衔接用，若无则忽略）】
{prev_tail}

【本segment原文（最高权重，所有事件和结果必须保留）】
{original_text}

请按输出格式要求，先规划台词结构，再输出改编文本：
- 台词必须用标准格式
- 补充台词标注（补），还原台词标注（还原）
- 字数控制在约束范围内
- drama_text直接输出正文，不加标题"""


def _build_dialogue_style_hint(chars_init: dict) -> str:
    """
    从chars_init动态生成台词改写指令。
    包含：speech_style（文字描述）+ dialogue_samples（原文样本）
    样本是最直接的风格证据，LLM模仿样本比描述更准确。
    """
    if not chars_init:
        return "（无人物档案，保持原文台词语气）"

    SKIP_VALUES = {"待补充", "（台词样本不足，待补充）", ""}
    lines = []
    for name, info in chars_init.items():
        style       = info.get("speech_style", "").strip()
        personality = info.get("personality", "").strip()
        samples     = info.get("dialogue_samples", [])

        # 有样本或有风格描述才输出
        if style in SKIP_VALUES and not samples:
            continue

        block = f"  {name}"
        if personality and personality not in SKIP_VALUES:
            block += f"（{personality}）："
        else:
            block += "："

        # 台词风格文字描述
        if style and style not in SKIP_VALUES:
            block += f"\n    风格描述：{style}"

        # 原文台词样本（最多3条，最直接的风格证据）
        valid_samples = [s.strip() for s in samples if s.strip()][:3]
        if valid_samples:
            samples_str = " / ".join(f"「{s}」" for s in valid_samples)
            block += f"\n    原文样本：{samples_str}"
            block += f"\n    改写要求：模仿以上样本的语气和节奏，控制每句≤20字"
        else:
            block += f"\n    改写要求：按风格描述改写，控制每句≤20字"

        lines.append(block)

    if not lines:
        return "（人物台词风格待补充，按原文语气处理，控制每句≤20字）"
    return "\n".join(lines)
def dramatize_single_segment(
    segment: dict,
    tone: str,
    chars_init: dict | None,
    qa_pairs: list[dict],
    prev_tail: str = "",
    segment_structure: dict | None = None,
) -> dict:
    """
    Step-E：单个segment漫剧化改编
    输出：drama_text + dialogue_structure（台词骨架）
    """
    from script_analyzer import format_chars_for_prompt
    chars_block         = format_chars_for_prompt(chars_init or {})
    dialogue_style_hint = _build_dialogue_style_hint(chars_init or {})
    confirmed_qa        = _build_confirmed_qa(qa_pairs)
    structure_desc      = (segment_structure or {}).get("structure_desc", "")

    hook_type = segment.get("hook_type", "null")
    if hook_type == "opening_hook":
        hook_type_desc = (
            "【首段铁律】第1句必须用强反差钩子：\n"
            "  当前原文最糟糕处境 vs 故事论调暗示的最终走向并置\n"
            "  禁止：平淡场景描写、时间地点交代、情绪平稳开场"
        )
    elif hook_type == "ending_suspense":
        hook_type_desc = (
            "【尾段铁律】最后2句必须留悬念：\n"
            "  基于本段最后事件自然延伸，不凭空引入新内容\n"
            "  禁止：情绪完全释放、交代太多"
        )
    else:
        hook_type_desc = "常规段落，情绪要有起伏，至少一个情绪高点"

    user = (_DRAMATIZE_USER
        .replace("{tone}",                tone)
        .replace("{core_conflict}",       (segment_structure or {}).get("core_conflict", ""))
        .replace("{emotional_core}",      (segment_structure or {}).get("emotional_core", ""))
        .replace("{structure_desc}",      structure_desc)
        .replace("{confirmed_qa}",        confirmed_qa)
        .replace("{chars_block}",         chars_block)
        .replace("{dialogue_style_hint}", dialogue_style_hint)
        .replace("{segment_name}",        segment.get("segment_name", ""))
        .replace("{segment_function}",    segment.get("segment_function", ""))
        .replace("{start_state}",         segment.get("start_state", ""))
        .replace("{end_state}",           segment.get("end_state", ""))
        .replace("{key_event}",           segment.get("key_event", ""))
        .replace("{hook_type_desc}",      hook_type_desc)
        .replace("{prev_tail}",           prev_tail or "（无，这是第一段）")
        .replace("{original_text}",       segment.get("original_text", ""))
    )

    raw_output = call_llm(user, _DRAMATIZE_SYSTEM)

    # 解析两个输出部分
    drama_text, dialogue_structure = _parse_dramatize_output(raw_output)

    return {
        **segment,
        "drama_text":          drama_text,
        "dialogue_structure":  dialogue_structure,
    }


def _parse_dramatize_output(raw: str) -> tuple[str, list[dict]]:
    """
    解析dramatize输出的两个部分：
    ===DRAMA_TEXT=== 和 ===DIALOGUE_STRUCTURE===
    """
    drama_text         = ""
    dialogue_structure = []

    if "===DRAMA_TEXT===" in raw and "===DIALOGUE_STRUCTURE===" in raw:
        parts = raw.split("===DIALOGUE_STRUCTURE===")
        ds_raw = parts[1].strip() if len(parts) > 1 else ""

        drama_parts = parts[0].split("===DRAMA_TEXT===")
        drama_text  = drama_parts[1].strip() if len(drama_parts) > 1 else parts[0].strip()

        # 解析dialogue_structure JSON
        ds_raw = _strip_json(ds_raw)
        s = ds_raw.find("[")
        e = ds_raw.rfind("]")
        if s != -1 and e != -1:
            try:
                dialogue_structure = json.loads(ds_raw[s:e+1])
            except Exception:
                dialogue_structure = []
    else:
        # 没有分隔线，整个输出作为drama_text
        drama_text = raw.strip()

    return drama_text, dialogue_structure


def dramatize_all_segments(
    segments: list[dict],
    tone: str,
    chars_init: dict | None,
    qa_pairs: list[dict],
    segment_structure: dict | None = None,
) -> list[dict]:
    """Step-E：所有segment并发漫剧化改编"""
    total   = len(segments)
    results = [None] * total
    lock    = threading.Lock()

    def worker(idx: int, seg: dict, prev_tail: str):
        try:
            result = dramatize_single_segment(
                segment           = seg,
                tone              = tone,
                chars_init        = chars_init,
                qa_pairs          = qa_pairs,
                prev_tail         = prev_tail,
                segment_structure = segment_structure,
            )
            with lock:
                results[idx] = result
        except Exception as e:
            print(f"[refine] segment {seg.get('segment_id')} 改编失败: {e}")
            with lock:
                results[idx] = {**seg, "drama_text": seg.get("original_text", "")}

    threads = []
    for i, seg in enumerate(segments):
        prev_tail = segments[i-1].get("original_text", "")[-100:] if i > 0 else ""
        t = threading.Thread(target=worker, args=(i, seg, prev_tail), daemon=True)
        threads.append(t)

    for t in threads: t.start()
    for t in threads: t.join()

    return [r for r in results if r is not None]


# ════════════════════════════════════════════════════════════════
# Step-F — 场景细分（并发）
# ════════════════════════════════════════════════════════════════

_SCENE_SPLIT_SYSTEM = """你是漫剧场景分析师，在叙事模块内识别场景切换。

# 场景定义
一个具体的空间位置或视觉焦点区域，镜头在这个区域内连续运动。
例：祠堂供桌前 / 主位太师椅处 / 侧席嫂嫂座位区 / 厅门入口处

# 切换判断

必须切（强触发）：
  旁白/场景描写明确说明转移到另一区域
  人物明确移动到另一空间并开始新的行动
  视觉焦点从A区域切换到B区域，且停留时间较长

不切（保持连续）：
  同一场对话中的来回镜头（你一句我一句）
  台词还没说完
  同一事件的连续反应链（A做了→B反应→C反应，这是一个场景）
  短暂的视线转移（不是真正的场景切换）

# 数量控制
一个模块通常1-3个场景
超过3个说明drama_text本身太散，强制合并到最近的场景
单场景模块输出只含1个元素的数组

# 输出要求
scene_name要具体，能让人想象出具体画面
space_desc包含光影特征，为Tab3场景资产生图提供基础"""

_SCENE_SPLIT_USER = """【叙事模块信息】
segment：{segment_name}
叙事功能：{segment_function}

【本segment台词结构规划（供场景分配参考）】
{dialogue_structure_hint}

【改编后文本（drama_text）】
{drama_text}

请识别这个segment内的场景，输出严格JSON数组：
[{{
  "scene_name":        "场景名（4-8字，具体空间位置，能让人想象画面）",
  "space_desc":        "空间光影氛围（一句话，包含光源特征，15字内）",
  "scene_text":        "本场景对应的drama_text原文片段，原样复制不改动",
  "chars_in_scene":    ["出现在本场景的具名角色名列表"],
  "dominant_emotion":  "本场景主情绪（2-4字，如：压抑对峙/温情流露/愤怒爆发）",
  "dialogue_count":    本场景预计对白行数（整数）
}}]

要求：
  所有scene_text首尾相连必须等于完整drama_text
  scene_name不能是泛称（禁止：室内/场景/某处/这里）
  最多输出3个场景，超过3个则合并相近场景
  chars_in_scene只填有名字的具名角色"""


def split_scenes_from_segment(drama_segment: dict, known_chars: list | None = None) -> list[dict]:
    """Step-F：单个segment场景细分"""
    drama_text = drama_segment.get("drama_text", "")
    if not drama_text.strip():
        return []

    # 构建dialogue_structure_hint供场景分配参考
    ds_all = drama_segment.get("dialogue_structure", [])
    if ds_all:
        ds_lines = [
            f"  {d.get('order','')}. [{d.get('type','')}] "
            f"{d.get('speaker','')} — {d.get('trigger','')} | {d.get('style_note','')}"
            for d in ds_all
        ]
        ds_hint = "\n".join(ds_lines)
    else:
        ds_hint = "（无台词结构规划）"

    # 加角色名单约束
    chars_constraint = ""
    if known_chars:
        chars_constraint = (
            f"\n\n【已知角色名单】以下是本故事所有具名角色，"
            f"chars_in_scene和speaker只能从这个列表中选取，不得自造新名字：\n"
            + "、".join(known_chars)
        )

    user = (_SCENE_SPLIT_USER
        .replace("{segment_name}",          drama_segment.get("segment_name", ""))
        .replace("{segment_function}",      drama_segment.get("segment_function", ""))
        .replace("{dialogue_structure_hint}", ds_hint)
        .replace("{drama_text}",            drama_text + chars_constraint)
    )

    raw = call_llm(user, _SCENE_SPLIT_SYSTEM)
    raw = _strip_json(raw)

    start = raw.find("[")
    end   = raw.rfind("]")
    if start == -1 or end == -1:
        return _fallback_single_scene(drama_segment)

    try:
        scenes = json.loads(raw[start:end+1])
        valid  = [
            s for s in scenes
            if isinstance(s, dict)
            and s.get("scene_name", "").strip()
            and s.get("scene_text", "").strip()
        ]
        if not valid:
            return _fallback_single_scene(drama_segment)

        result = []
        n_scenes = len(valid)
        for i, s in enumerate(valid):
            # 按顺序把dialogue_structure分配给scene
            # 简单策略：均分，最后一个scene拿剩余的
            ds_all   = drama_segment.get("dialogue_structure", [])
            total_ds = len(ds_all)
            if n_scenes <= 1:
                scene_ds = ds_all
            else:
                per = total_ds // n_scenes
                start_idx = i * per
                end_idx   = start_idx + per if i < n_scenes - 1 else total_ds
                scene_ds  = ds_all[start_idx:end_idx]

            result.append({
                "segment_id":       drama_segment.get("segment_id", 0),
                "segment_name":     drama_segment.get("segment_name", ""),
                "segment_function": drama_segment.get("segment_function", ""),
                "hook_type":        drama_segment.get("hook_type", "null") if i == 0 else "null",
                "scene_name":       s["scene_name"],
                "space_desc":       s.get("space_desc", ""),
                "scene_text":       s["scene_text"],
                "original_text":    drama_segment.get("original_text", ""),
                "dialogue_structure": scene_ds,
                # 结构化新字段
                "chars_in_scene":   s.get("chars_in_scene", []),
                "dominant_emotion": s.get("dominant_emotion", ""),
                "dialogue_count":   s.get("dialogue_count", 0),
            })

        # 尾段ending_suspense给最后一个scene
        if drama_segment.get("hook_type") == "ending_suspense" and result:
            result[-1]["hook_type"] = "ending_suspense"

        return result

    except Exception:
        return _fallback_single_scene(drama_segment)


def _fallback_single_scene(drama_segment: dict) -> list[dict]:
    return [{
        "segment_id":         drama_segment.get("segment_id", 0),
        "segment_name":       drama_segment.get("segment_name", ""),
        "segment_function":   drama_segment.get("segment_function", ""),
        "hook_type":          drama_segment.get("hook_type", "null"),
        "scene_name":         drama_segment.get("segment_name", "场景"),
        "space_desc":         "",
        "scene_text":         drama_segment.get("drama_text", ""),
        "original_text":      drama_segment.get("original_text", ""),
        "dialogue_structure": drama_segment.get("dialogue_structure", []),
        "chars_in_scene":     [],
        "dominant_emotion":   "",
        "dialogue_count":     0,
    }]


def split_all_scenes(drama_segments: list[dict], chars_init: dict | None = None) -> list[dict]:
    """Step-F：所有segment并发场景细分，输出带scene_id的scenes列表"""
    total   = len(drama_segments)
    results = [None] * total
    lock    = threading.Lock()

    known_chars = list(chars_init.keys()) if chars_init else []

    def worker(idx: int, seg: dict):
        try:
            scenes = split_scenes_from_segment(seg, known_chars=known_chars)
            with lock:
                results[idx] = scenes
        except Exception as e:
            print(f"[refine] segment {seg.get('segment_id')} 场景细分失败: {e}")
            with lock:
                results[idx] = _fallback_single_scene(seg)

    threads = []
    for i, seg in enumerate(drama_segments):
        t = threading.Thread(target=worker, args=(i, seg), daemon=True)
        threads.append(t)

    for t in threads: t.start()
    for t in threads: t.join()

    # 拍平并分配全局scene_id
    all_scenes = []
    scene_id   = 1
    for scene_list in results:
        if scene_list:
            for s in scene_list:
                s["scene_id"] = scene_id
                all_scenes.append(s)
                scene_id += 1

    return all_scenes


# ════════════════════════════════════════════════════════════════
# 工具函数
# ════════════════════════════════════════════════════════════════

def _build_yesno_history(qa_pairs: list[dict]) -> str:
    if not qa_pairs:
        return "（暂无，这是第一个问题）"
    lines = []
    for i, qa in enumerate(qa_pairs, 1):
        answer_str = "是" if qa.get("answer") else "否"
        means      = qa.get("means", "")
        lines.append(f"Q{i}: {qa['question']}")
        lines.append(f"A{i}: {answer_str}（{means}）")
    return "\n".join(lines)


def _build_refine_history(qa_pairs: list[dict]) -> str:
    if not qa_pairs:
        return "（暂无）"
    lines = []
    for i, qa in enumerate(qa_pairs, 1):
        answer = qa.get("custom") or qa.get("chosen_option", "")
        lines.append(f"Q{i}: {qa['question']}")
        lines.append(f"A{i}: {answer}")
    return "\n".join(lines)


def _build_confirmed_qa(qa_pairs: list[dict]) -> str:
    if not qa_pairs:
        return "（无，完全基于原文和论调）"
    lines = []
    for qa in qa_pairs:
        answer = qa.get("custom") or qa.get("chosen_option", "")
        if qa.get("question") and answer:
            lines.append(f"• {qa['question']}")
            lines.append(f"  → {answer}")
    return "\n".join(lines)


def _parse_tone_question(text: str) -> dict | None:
    text = _strip_codeblock(text)
    s, e = text.find("{"), text.rfind("}")
    if s == -1 or e == -1:
        return None
    try:
        data = json.loads(text[s:e+1])
        q = str(data.get("question", "")).strip()
        if not q:
            return None
        return {
            "question":  q,
            "yes_means": str(data.get("yes_means", "")).strip(),
            "no_means":  str(data.get("no_means", "")).strip(),
            "answer":    None,
        }
    except Exception:
        return None


def _parse_refine_question(text: str) -> dict | None:
    text = _strip_codeblock(text)
    s, e = text.find("{"), text.rfind("}")
    if s == -1 or e == -1:
        return None
    try:
        data    = json.loads(text[s:e+1])
        q       = str(data.get("question", "")).strip()
        options = data.get("options", [])
        if not q or len(options) < 3:
            return None
        return {
            "question":     q,
            "which_module": str(data.get("which_module", "")).strip(),
            "options":      [str(o).strip() for o in options[:3]],
            "chosen":       None,
            "custom":       "",
        }
    except Exception:
        return None


def _parse_options(text: str, fallback: list[str]) -> list[str]:
    text = _strip_codeblock(text)
    s, e = text.find("["), text.rfind("]")
    if s == -1 or e == -1:
        return fallback
    try:
        data = json.loads(text[s:e+1])
        opts = [str(o).strip() for o in data if isinstance(o, str) and str(o).strip()]
        return opts[:3] if len(opts) >= 3 else fallback
    except Exception:
        return fallback


def _strip_json(text: str) -> str:
    text = text.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    return text.strip()


def _strip_codeblock(text: str) -> str:
    text = text.strip()
    if "```" in text:
        lines = [l for l in text.split("\n") if not l.strip().startswith("```")]
        text  = "\n".join(lines).strip()
    return text