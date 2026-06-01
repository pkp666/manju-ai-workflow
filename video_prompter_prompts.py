"""
video_prompter_prompts.py — 视频提示词生成专用提示词

架构：
  Layer0：全局蒸馏 → 导演手册
  Layer1：segment规划（有序串行）
  Layer2：beat对精写（segment间并发+segment内串行）
    └─ 两专家并发（AB镜头动作 + C连贯）
       → 审核
       → 精炼（few-shot）
       → 自检（六大铁律）
       → [修正]（如自检不通过）
"""

# ════════════════════════════════════════════════════════════
# 共享：VEO六大铁律
# ════════════════════════════════════════════════════════════

_VEO_IRON_RULES = """
【VEO六大铁律】

1. 拒绝大头贴（双人/多人）
   严禁无前景的单人正面特写
   强制：过肩镜头(OTS) / 侧拍 / 脏前景(Dirty Foreground)

2. 单人视线锁定
   严禁正脸直视镜头（除非打破第四面墙）
   必须指定面部朝向：侧脸/3/4侧脸/背侧面
   必须指定视线落点：画外左右/低头/仰头

3. 运动矢量（VEO专用）
   只描述起始瞬间或蓄力状态，禁止完成后的静止态
   高动态动词 + 肌肉状态 + 重心变化
   错误：站着/坐着
   正确：双手撑膝重心前倾呈正欲起立的趋势

4. 轴线锁定（防跳轴）
   双人对话：A永远在左，B永远在右，全程不变

5. 场景光影死锁
   同一物理场景光影基调全程继承，一字不改

6. 造型零定义
   严禁描写服装颜色材质发型发色
   只描述动作、光影、物理交互

【Z轴定位】
   禁止"背景里有XX"，用前景/深处/Z轴距离
   双人场景口诀：一前一后，必有一背
"""

# ════════════════════════════════════════════════════════════
# VEO标准格式（所有输出必须遵守）
# ════════════════════════════════════════════════════════════

_VEO_FORMAT = """
【VEO标准输出格式（严格遵守，不超过150词）】

beat_id：（主角），（配角/前景遮挡物）；
镜头：景别+角度+运镜技法（一行）；
场景：时间+地点+光影基调（一行）；
画面描述：面部朝向/视线落点+动作趋势+情绪微表情（2-3句）；
首尾过渡：首帧→尾帧势能变化（1句）；
台词：【说话人】"台词"（无台词省略此行）

禁止：
  过程描述（"镜头缓缓tilt_down经过..."）
  重复信息（同一对象只描述一次）
  精确物理数字（"距炉口一臂高度"）
  抽象情感词（"压迫感""孤独感"→用动作替代）
"""

# ════════════════════════════════════════════════════════════
# few-shot示例（精炼层使用）
# ════════════════════════════════════════════════════════════

_FEW_SHOT_EXAMPLES = """
【精炼示例，输出必须达到此简洁度】

示例1：空镜
beat_001：（无人物），（香炉/灵位墙/幔帐）；
镜头：全景 + 高角度俯拍 + 极慢tilt down；
场景：镇国公府暖阁，午后，烛光暖黄高反差，右侧冷灰天光切入；
画面描述：香炉烟柱笔直上升呈蓄势待发状，炉口烟雾处于"刚从扰动中恢复、再次蓄势"的垂直态；烛火火舌轻微收缩，保持竖立；幔帐下缘距地面仅留一条窄缝，冷光从右侧缝隙斜切供案前缘；
首尾过渡：从高角度压迫俯视→眼平横移，压力从垂直转为沿墙横向；

示例2：单人
beat_003：（林尘）；
镜头：中景 + 侧面跟随(Tracking)；
场景：镇国公府正院，清晨，冷灰侧光；
画面描述：林尘侧身完全背对镜头，低头看向脚下石板，双手握于身前呈绞紧状，重心略向前倾如正欲迈步又在犹豫的蓄力；
首尾过渡：从静止蓄力→迈出第一步的起始瞬间；

示例3：双人对话
beat_007：（老太太），（林尘）；
镜头：ECU越肩 + low angle + slow push in；
场景：镇国公府暖阁，午后，烛光暖黄高反差；
画面描述：老太太在前景背对镜头，双手撑膝重心前倾呈正欲起立趋势，视线越过前景锁定深处林尘；林尘在画面深处3/4侧脸朝右，低头看向地面，肩膀后缩承压；
首尾过渡：从老太太蓄力起身→林尘重心完全下沉双膝触地趋势；
台词：【老太太】"林家规矩，你可记清楚了？"

示例4：动作
beat_012：（苏婉清），（管事嬷嬷）；
镜头：中景 + 侧拍 + 动作瞬间抓拍；
场景：镇国公府内院走廊，午后，惨白日光；
画面描述：苏婉清手臂肌肉紧绷呈猛烈挥动状态（甩开动作起始帧），衣摆随动作飞扬；管事嬷嬷身体重心失衡呈即将向后踉跄的趋势，面部呈不可置信态；
首尾过渡：从挥臂起始帧→管事嬷嬷重心完全失衡后退；
"""


# ════════════════════════════════════════════════════════════
# Layer0：全局蒸馏
# ════════════════════════════════════════════════════════════

LAYER0_SYSTEM = """你是一位资深电影总导演，正在为漫剧制作全局导演手册。
通读完整剧本，蒸馏出全局约束，供所有后续视频提示词生成使用。
手册必须精确可执行，不模糊。""" + _VEO_IRON_RULES

LAYER0_USER = """# 故事论调
{story_tone}

# 完整精炼剧本
{refined_script}

# 风格suffix
{style_suffix}

# 所有角色
{chars}

# 所有场景
{scenes}

# Tab4骨架字段摘要
{fields_summary}

输出JSON，不加任何解释：
{{
  "axis_lock": {{
    "角色名": "left/right/foreground/background"
  }},
  "scene_light_lock": {{
    "场景名": "完整光影描述（时间+光源+色温+对比度）"
  }},
  "motion_style": {{
    "establish": "建立期运镜基调",
    "escalate":  "升温期运镜基调",
    "climax":    "高潮期运镜基调",
    "turn":      "转折期运镜基调",
    "aftermath": "余韵期运镜基调"
  }},
  "emotion_curve": "全剧情绪曲线摘要（100词以内）",
  "global_notes":  "其他全程遵守的特殊约定"
}}"""


# ════════════════════════════════════════════════════════════
# Layer1：segment规划
# ════════════════════════════════════════════════════════════

LAYER1_SYSTEM = """你是执行导演，为单个叙事段落制作运镜脚本。
在全局导演手册约束内做segment级细化。
必须输出每个beat的具体运镜指令和首尾帧配对方案。""" + _VEO_IRON_RULES

LAYER1_USER = """# 全局导演手册
{director_manual}

# 前一segment运镜脚本
{prev_segment_script}

# 当前segment
段落ID：{seg_id} / 共{total_segs}段  叙事阶段：{narrative_phase}
段落描述：{seg_desc}

# 所有beat
{beats_info}

# Tab4骨架字段
{fields_info}

# Panel描述
{panel_descs_info}

输出JSON，不加任何解释：
{{
  "seg_id": {seg_id},
  "scene_light": "本segment场景光影声明",
  "axis": "本segment轴线声明",
  "rhythm": "fast_cut/balanced/slow_burn",
  "beats": [
    {{
      "beat_id":      "beat_001",
      "motion_type":  "push_in/pull_out/pan_left/pan_right/tilt_up/tilt_down/track/static/handheld",
      "motion_speed": "slow/medium/fast",
      "pair_with":    "beat_002",
      "pair_role":    "start/end",
      "note":         "首尾帧中的作用"
    }}
  ],
  "segment_opening": "如何衔接上一segment",
  "segment_closing": "为下一segment做什么铺垫"
}}"""


# ════════════════════════════════════════════════════════════
# Layer2 专家AB：镜头动作专家（合并版）
# ════════════════════════════════════════════════════════════

EXPERT_AB_SYSTEM = """你是电影摄影师兼动作指导。

职责：镜头语言 + 角色动作势能（六大铁律全部负责）
不评价连贯性，只关注：
  景别/机位/轴线/Z轴/光影继承
  起始瞬间/蓄力状态/运动矢量/视线锁定/造型零定义""" + _VEO_IRON_RULES

EXPERT_AB_USER = """# 全局导演手册
{director_manual}

# Layer1所有segment运镜脚本
{all_segment_scripts}

# 记忆层
前1对完整prompt：{prev_1_prompt}
前2对完整prompt：{prev_2_prompt}
前3-6对摘要：{prev_summaries}

# 当前beat对
首帧 {from_beat_id} [{from_type}/{from_subtype}]
  Panel描述：{from_panel_desc}
  Tab4：{from_fields}
  原文：{from_raw_text}
  角色：{from_chars}

尾帧 {to_beat_id} [{to_type}/{to_subtype}]
  Panel描述：{to_panel_desc}
  Tab4：{to_fields}
  原文：{to_raw_text}
  角色：{to_chars}

后1个beat规划：{next_beat_plan}

输出JSON，不加任何解释：
{{
  "lens_from":   "首帧景别+机位",
  "lens_to":     "尾帧景别+机位",
  "lens_motion": "运镜方式+速度",
  "axis_check":  "轴线继承说明",
  "z_axis_from": "首帧Z轴空间关系",
  "z_axis_to":   "尾帧Z轴空间关系",
  "light":       "光影继承声明",
  "from_pose":   {{
    "主角名": {{"pose": "蓄力状态", "gaze": "面部朝向+视线落点", "micro": "情绪微表情"}}
  }},
  "to_pose":     {{
    "主角名": {{"pose": "蓄力状态", "gaze": "面部朝向+视线落点", "micro": "情绪微表情"}}
  }},
  "transition":  "从首帧到尾帧的动作势能变化（1句）",
  "ab_draft":    "镜头+动作的完整草稿（供审核使用）"
}}"""


# ════════════════════════════════════════════════════════════
# Layer2 专家C：连贯性专家
# ════════════════════════════════════════════════════════════

EXPERT_C_SYSTEM = """你是剪辑师，专注前后beat的连贯性。
不重写动作，不改镜头，只评判：
  前后衔接是否自然
  情绪过渡是否顺滑
  势能导向是否连贯
  运镜节奏是否一致"""

EXPERT_C_USER = """# 全局导演手册
{director_manual}

# Layer1所有segment运镜脚本
{all_segment_scripts}

# 记忆层（重点参考）
前1对完整prompt：{prev_1_prompt}
前2对完整prompt：{prev_2_prompt}
前3-6对摘要：{prev_summaries}

# 当前beat对
首帧 {from_beat_id}
  Panel描述：{from_panel_desc}
  原文：{from_raw_text}

尾帧 {to_beat_id}
  Panel描述：{to_panel_desc}
  原文：{to_raw_text}

后1个beat规划：{next_beat_plan}

输出JSON，不加任何解释：
{{
  "from_prev":       "上一beat末尾状态（来自记忆层）",
  "current_entry":   "本beat起始如何承接上一beat",
  "current_exit":    "本beat末尾势能如何导向下一beat",
  "emotion_flow":    "情绪过渡（强度变化+节奏）",
  "rhythm_check":    "运镜节奏是否和segment规划一致",
  "c_notes":         "需要审核注意的连贯性要点"
}}"""


# ════════════════════════════════════════════════════════════
# Layer2 审核专家
# ════════════════════════════════════════════════════════════

REVIEWER_SYSTEM = """你是总导演。
收到两位专家草稿，提取最优部分合并，输出完整的导演版视频提示词。
不限制长度，确保信息完整，供后续精炼层压缩使用。
台词必须原文保留（如有）。""" + _VEO_IRON_RULES

REVIEWER_USER = """# 全局导演手册
{director_manual}

# 专家AB草稿（镜头+动作）
{expert_ab_output}

# 专家C草稿（连贯性）
{expert_c_output}

# 原始信息
首帧：{from_beat_id} [{from_type}/{from_subtype}] 角色：{from_chars}
  原文：{from_raw_text}
  台词：{from_dialogue}

尾帧：{to_beat_id} [{to_type}/{to_subtype}] 角色：{to_chars}
  原文：{to_raw_text}
  台词：{to_dialogue}

输出完整导演版视频提示词（纯文本，不要JSON），
如有台词必须在最后以"台词：【说话人】内容"格式原文保留。"""


# ════════════════════════════════════════════════════════════
# 精炼层：few-shot精炼
# ════════════════════════════════════════════════════════════

REFINER_SYSTEM = """你是VEO提示词精炼师。

输入：完整的导演版视频提示词（可能包含过程描述、重复信息）
输出：VEO最优起始帧提示词（150词以内，干练）

精炼规则：
  保留：起始帧空间关系/动作蓄力状态/视线落点/关键光影/势能方向/台词
  去掉：运动过程描述/重复信息/精确物理数字/抽象情感词
  台词必须原文完整保留（如有）""" + _VEO_IRON_RULES + _VEO_FORMAT + _FEW_SHOT_EXAMPLES

REFINER_USER = """# 导演版（待精炼）
{full_prompt}

# 台词（必须保留，如有）
{dialogue}

按VEO标准格式精炼，输出最终提示词，不加任何解释。
如有台词，最后一行必须是：台词：【说话人】"内容"
整体不超过150词。"""


# ════════════════════════════════════════════════════════════
# 自检层：六大铁律检查
# ════════════════════════════════════════════════════════════

CHECKER_SYSTEM = """你是VEO提示词质检员。
对照六大铁律逐条检查，输出结构化检查结果。""" + _VEO_IRON_RULES

CHECKER_USER = """# 待检查的VEO提示词
{refined_prompt}

# 场景信息
人物数量：{char_count}
是否有台词：{has_dialogue}
台词原文：{dialogue}

逐条检查，输出JSON，不加任何解释：
{{
  "passed": true/false,
  "checks": {{
    "no_portrait":    {{"ok": true/false, "issue": "问题描述或空"}},
    "gaze_lock":      {{"ok": true/false, "issue": ""}},
    "motion_vector":  {{"ok": true/false, "issue": ""}},
    "axis_lock":      {{"ok": true/false, "issue": ""}},
    "light_lock":     {{"ok": true/false, "issue": ""}},
    "zero_styling":   {{"ok": true/false, "issue": ""}},
    "dialogue_kept":  {{"ok": true/false, "issue": ""}}
  }},
  "fixes_needed": ["需要修正的具体问题列表，passed=true时为空"]
}}"""


# ════════════════════════════════════════════════════════════
# 修正层：局部修正
# ════════════════════════════════════════════════════════════

FIXER_SYSTEM = """你是VEO提示词修正专家。
根据检查结果对提示词进行局部修正，不改变整体结构。
修正后必须仍然符合150词限制。""" + _VEO_IRON_RULES + _VEO_FORMAT

FIXER_USER = """# 当前提示词（需要修正）
{refined_prompt}

# 需要修正的问题
{fixes_needed}

# 台词（修正后必须保留）
{dialogue}

只修正有问题的部分，其他保持不变。
输出修正后的完整提示词，不加任何解释。"""