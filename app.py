import json
import os
import re as _re
import streamlit as st
import pandas as pd
from script_parser import parse_script
from style_config import parse_style_from_text, build_style_from_selections, get_art_styles, get_options

from pathlib import Path
from asset_manager import (
    # 提取工具
    extract_characters_raw, clean_characters,
    extract_scenes, extract_periods_from_beats,

    # 生图函数
    generate_base_prompts_3, optimize_base_prompts_3,
    generate_period_prompts_3,
    generate_scene_prompts_3,
    generate_prop_prompt,
    optimize_prompt,

    # 进化引擎
    make_history_snapshot,
    evolve_char, evolve_scene, evolve_prop_or_period,

    # 状态初始化
    init_char_state, init_period_state,
    init_scene_state, init_prop_state,

    # GachaConfig工厂
    GachaConfig,
    make_char_config, make_period_config,
    make_scene_config, make_prop_config,

    # 批量操作
    batch_gen_prompts,

    # Tab4/Tab5工具
    bind_assets, build_asset_context,
    build_prompt_en, infer_narrative_phase,
)
from debug_logger import save_tab_snapshot
from state.project_io import (
    build_project_from_state,
    has_project_data,
    load_project_file,
    load_project_into_state,
    project_json,
)
from script_refine import (
    next_tone_question, build_tone, patch_tone,
    next_refine_question, refresh_options,
    analyze_segment_structure,
    split_by_segments,
    dramatize_all_segments, dramatize_single_segment,
    split_all_scenes,
)
from shot_optimizer import (
    optimize_all_segments, optimize_segment,
    generate_shot_prompts_3, optimize_shot_prompts_3,
    bind_assets as so_bind_assets,
    missing_assets as so_missing_assets,
    infer_narrative_phase as so_infer_narrative_phase,
    build_asset_context as so_build_asset_context,
    build_prompt_en as so_build_prompt_en,
)

st.set_page_config(page_title="漫剧工程管理器", layout="wide")
st.title("漫剧工程管理器")




# ── 侧边栏：随时保存项目 ──────────────────────────────────────
with st.sidebar:
    st.markdown("### 💾 项目存档")

    # ── 保存：任何时候都可以保存 ──────────────────────────
    if has_project_data(st.session_state):
        project = build_project_from_state(st.session_state)

        beats_n   = len(project["beats"])
        chars_n   = len(project["chars"])
        locked_c  = sum(1 for v in project["char_assets"].values()
                        if v.get("base",{}).get("status")=="locked")
        locked_s  = sum(1 for v in project["scene_assets"].values()
                        if v.get("status")=="locked")
        prompted_c = sum(1 for v in project["char_assets"].values()
                         if any(v.get("base",{}).get("prompts",[])))

        st.caption(f"第零步: {'✅' if project['story_tone'] else '⬜'}论调  "
           f"{'✅' if project['scenes'] else '⬜'}{len(project['scenes'])}个scene")
        st.caption(f"第一步: {'✅' if beats_n else '⬜'} {beats_n} beats")
        st.caption(f"第三步: 人物 {locked_c}/{chars_n}锁定  场景 {locked_s}锁定")

        st.download_button(
            "💾 保存项目",
            data=project_json(project),
            file_name="project.json",
            mime="application/json",
            type="primary",
            use_container_width=True,
        )
    else:
        st.caption("开始使用后即可保存")

    st.divider()

    # ── 加载：放在sidebar顶部，随时可加载 ─────────────────
    st.markdown("### 📂 加载存档")
    _proj_file = st.file_uploader(
        "选择 project.json",
        type=["json"],
        key="sidebar_proj_upload",
        label_visibility="collapsed",
    )
    if _proj_file:
        _file_id = f"{_proj_file.name}_{_proj_file.size}"
        if st.session_state.get("_loaded_proj_id") != _file_id:
            try:
                _stats = load_project_into_state(
                    load_project_file(_proj_file),
                    st.session_state,
                    _file_id,
                )
                st.success(
                    f"加载成功 ✅ beats:{_stats['beats']}  "
                    f"人物:{_stats['chars']}  场景:{_stats['scenes']}"
                )
                if _stats["missing_images"]:
                    st.warning(f"⚠️ {_stats['missing_images']} 张本地图片缺失，已重置")
                st.rerun()
            except Exception as e:
                st.error(f"加载失败：{e}")

    st.divider()
    st.markdown("### 📖 故事论调")
    st.caption("可直接填入，避免每次重新问答")
    tone_input = st.text_area(
        "论调",
        value=st.session_state.get("story_tone", ""),
        height=100,
        key="sidebar_tone_input",
        label_visibility="collapsed",
        placeholder="直接粘贴故事论调，或完成第零步自动生成...",
    )
    if st.button("✅ 应用论调", key="sidebar_tone_apply",
                 disabled=not tone_input.strip()):
        st.session_state["story_tone"] = tone_input.strip()
        if "rs" in st.session_state:
            st.session_state["rs"]["tone_text"]      = tone_input.strip()
            st.session_state["rs"]["tone_confirmed"] = True
        st.success("论调已应用，请到第零步完成第二步精炼剧本")
        st.rerun()
    if st.session_state.get("story_tone"):
        st.caption(f"✅ 当前论调：{st.session_state['story_tone'][:40]}...")

    st.divider()
    st.caption("💡 提示词生成完成后，请复制到外部工具（Midjourney/ComfyUI等）生图，再上传结果")

tab0,tab1,tab2,tab3,tab4,tab5,tab6,tab7,tab8 = st.tabs([
    "第零步：剧本精炼","第一步：剧本切分","第二步：风格定稿",
    "第三步：人物/场景资产","第四步：分镜扩写","第五步：分镜生成",
    "第六步：单帧遗传","第七步：视频生成","📊 超级表格"
])
# ══════════════════════════════════════════════════════════════
# Tab0: 第零步 剧本精炼
# ══════════════════════════════════════════════════════════════
with tab0:
    st.subheader("第零步：剧本精炼")
    st.caption("输入原文 → 论调问答 → 叙事结构分析 → 问答补充 → 切分 → 漫剧化改编 → 场景细分")

    # ── 初始化 rs ────────────────────────────────────────────
    if "rs" not in st.session_state:
        st.session_state["rs"] = {
            "raw":                 "",
            # Step-A
            "tone_qa":             [],
            "tone_current":        None,
            "tone_text":           "",
            "tone_confirmed":      False,
            # Step-B
            "segment_structure":   {},
            # Step-C
            "refine_qa":           [],
            "refine_current":      None,
            "refine_confirmed":    False,
            # Step-D
            "segments":            [],
            # Step-E
            "drama_segments":      [],
            # Step-F
            "scenes":              [],
            # 完成标志
            "script_confirmed":    False,
        }
    rs = st.session_state["rs"]


    def _save():
        st.session_state["rs"] = rs

    # ── 快捷填入论调 ─────────────────────────────────────────
    with st.expander("⚡ 快捷填入论调（已有论调时跳过问答）",
                     expanded=not st.session_state.get("story_tone")):
        quick_tone = st.text_area(
            "直接填入论调",
            value=st.session_state.get("story_tone", ""),
            height=80,
            key="quick_tone_input",
            label_visibility="collapsed",
            placeholder="直接粘贴故事论调，跳过问答步骤...",
        )
        if st.button("✅ 应用论调", key="quick_tone_apply",
                     disabled=not quick_tone.strip()):
            st.session_state["story_tone"] = quick_tone.strip()
            rs["tone_text"]      = quick_tone.strip()
            rs["tone_confirmed"] = True
            _save()
            st.success("论调已应用")
            st.rerun()

    st.divider()

    # ══════════════════════════════════════════════════════════
    # 步骤1：输入原文 + 解析
    # ══════════════════════════════════════════════════════════
    st.markdown("### 步骤1：输入原文")

    raw_input = st.text_area(
        "粘贴原始剧本",
        value=rs["raw"],
        height=180,
        placeholder="粘贴原始剧本（草稿/大纲/完整剧本均可）...",
        key="rs_raw_input",
    )
    if raw_input != rs["raw"]:
        rs["raw"] = raw_input
        _save()

    chars_init = st.session_state.get("chars_init", {})
    s1a, s1b = st.columns([1, 3])
    with s1a:
        if st.button("🔍 解析原文", key="rs_analyze",
                    disabled=not rs["raw"].strip()):
            with st.spinner("并发解析人物台词 / 外貌 / 道具，约20秒..."):
                from script_analyzer import analyze_raw_script
                result = analyze_raw_script(
                    raw_script = rs["raw"],
                    story_tone = st.session_state.get("story_tone", ""),
                )
                st.session_state["chars_init"]      = result["chars_init"]
                st.session_state["appearance_init"] = result["appearance_init"]
                st.session_state["props_init"]      = result["props_init"]
                st.session_state["raw_script"]      = rs["raw"]
                chars_init = result["chars_init"]
                # 删掉：scenes_init / raw_dialogues（不再需要）
            _save()
            st.rerun()
    with s1b:
        if chars_init:
            appearance_init = st.session_state.get("appearance_init", {})
            props_init      = st.session_state.get("props_init", [])
            st.success(
                f"✅ 已解析：{len(chars_init)}个人物 · {len(props_init)}个道具"
            )
            col1, col2 = st.columns(2)
            with col1:
                with st.expander("查看人物台词风格", expanded=False):
                    for name, info in chars_init.items():
                        st.markdown(f"**{name}**（{info.get('role','')}）")
                        st.caption(f"台词风格：{info.get('speech_style','')}")
                        samples = info.get("dialogue_samples", [])
                        if samples:
                            st.caption("样本：" + "  /  ".join(f"「{s}」" for s in samples[:3]))
            with col2:
                with st.expander("查看角色外貌", expanded=False):
                    for name, ap in appearance_init.items():
                        st.markdown(f"**{name}**")
                        fields = [ap.get(k,"") for k in
                                ["features","trait","physique","hairstyle","outfit"] if ap.get(k)]
                        st.caption("，".join(fields) if fields else "（待提取）")
            with st.expander("查看道具", expanded=False):
                for p in props_init:
                    st.markdown(f"- **{p['name']}**")
                    if p.get("aliases"):
                        st.caption(f"别称：{'、'.join(p['aliases'])}")
                    st.caption(p.get("description",""))
        else:
            st.info("建议先解析原文再开始论调问答")

    st.divider()

    # ══════════════════════════════════════════════════════════
    # 步骤2：论调问答
    # ══════════════════════════════════════════════════════════
    st.markdown("### 步骤2：论调问答")
    st.caption("通过是/否问答确立故事总基调")

    if not rs["tone_confirmed"]:
        if not rs["tone_qa"] and not rs["tone_current"]:
            if st.button("▶ 开始论调问答", type="primary",
                         key="rs_tone_start",
                         disabled=not rs["raw"].strip()):
                with st.spinner("生成第一个问题..."):
                    from script_refine import next_tone_question
                    q = next_tone_question(rs["raw"], rs["tone_qa"])
                rs["tone_current"] = q
                _save()
                st.rerun()

        for i, qa in enumerate(rs["tone_qa"]):
            ans_label = "✅ 是" if qa["answer"] else "❌ 否"
            means     = qa["yes_means"] if qa["answer"] else qa["no_means"]
            st.markdown(f"**Q{i+1}. {qa['question']}**  →  {ans_label}")
            st.caption(f"意味着：{means}")

        cur = rs["tone_current"]
        if cur and cur.get("answer") is None:
            st.divider()
            st.markdown(f"**{cur['question']}**")
            y_col, n_col, skip_col, done_col = st.columns([1, 1, 1, 2])
            with y_col:
                if st.button("✅ 是", key="rs_tone_yes",
                             use_container_width=True, type="primary"):
                    cur["answer"] = True
                    rs["tone_qa"].append(cur)
                    rs["tone_current"] = None
                    _save()
                    with st.spinner("生成下一个问题..."):
                        from script_refine import next_tone_question
                        nxt = next_tone_question(rs["raw"], rs["tone_qa"])
                    rs["tone_current"] = nxt
                    _save()
                    st.rerun()
            with n_col:
                if st.button("❌ 否", key="rs_tone_no",
                             use_container_width=True):
                    cur["answer"] = False
                    rs["tone_qa"].append(cur)
                    rs["tone_current"] = None
                    _save()
                    with st.spinner("生成下一个问题..."):
                        from script_refine import next_tone_question
                        nxt = next_tone_question(rs["raw"], rs["tone_qa"])
                    rs["tone_current"] = nxt
                    _save()
                    st.rerun()
            with skip_col:
                if st.button("🔀 换问题", key="rs_tone_skip",
                             use_container_width=True):
                    with st.spinner("重新生成..."):
                        from script_refine import next_tone_question
                        nxt = next_tone_question(rs["raw"], rs["tone_qa"])
                    rs["tone_current"] = nxt
                    _save()
                    st.rerun()
            with done_col:
                if st.button("完成，生成论调 →", key="rs_tone_done",
                             disabled=not rs["tone_qa"], type="secondary"):
                    with st.spinner("生成故事论调..."):
                        from script_refine import build_tone
                        tone = build_tone(rs["raw"], rs["tone_qa"])
                    rs["tone_text"] = tone
                    _save()
                    st.rerun()

        elif cur is None and rs["tone_qa"] and not rs["tone_text"]:
            st.info("已有足够信息，可以生成论调了")
            if st.button("生成论调 →", type="primary", key="rs_tone_gen"):
                with st.spinner("生成故事论调..."):
                    from script_refine import build_tone
                    tone = build_tone(rs["raw"], rs["tone_qa"])
                rs["tone_text"] = tone
                _save()
                st.rerun()

        if rs["tone_text"]:
            st.divider()
            st.markdown("**故事论调**")
            st.text_area("", value=rs["tone_text"], height=120,
                         disabled=True, key="rs_tone_display",
                         label_visibility="collapsed")
            ok_col, edit_col = st.columns([1, 3])
            with ok_col:
                if st.button("✅ 满意，进入下一步", type="primary",
                             key="rs_tone_confirm"):
                    rs["tone_confirmed"] = True
                    st.session_state["story_tone"] = rs["tone_text"]
                    _save()
                    st.rerun()
            with edit_col:
                fb = st.text_input("不满意？输入修改意见", key="rs_tone_fb",
                                   label_visibility="collapsed",
                                   placeholder="例：这是女频，不是男频...")
                if st.button("修改论调", key="rs_tone_patch",
                             disabled=not fb.strip()):
                    with st.spinner("修改中..."):
                        from script_refine import patch_tone
                        rs["tone_text"] = patch_tone(rs["tone_text"], fb)
                    _save()
                    st.rerun()
    else:
        st.success(f"✅ 论调已确认：{rs['tone_text'][:60]}...")
        if st.button("↺ 重新问答", key="rs_tone_redo"):
            rs["tone_qa"]        = []
            rs["tone_current"]   = None
            rs["tone_text"]      = ""
            rs["tone_confirmed"] = False
            _save()
            st.rerun()

    # ══════════════════════════════════════════════════════════
    # 步骤3：叙事结构分析
    # ══════════════════════════════════════════════════════════
    if rs["tone_confirmed"]:
        st.divider()
        st.markdown("### 步骤3：叙事结构分析")
        st.caption("模型分析原文，识别叙事结构（segment划分）和模糊点")

        ss = rs.get("segment_structure", {})
        b1, b2 = st.columns([1, 3])
        with b1:
            if st.button(
                "🎬 分析叙事结构",
                key="rs_analyze_structure",
                type="primary" if not ss.get("modules") else "secondary",
                disabled=not rs["raw"].strip(),
            ):
                with st.spinner("叙事结构分析中，约15秒..."):
                    from script_refine import analyze_segment_structure
                    result = analyze_segment_structure(
                        raw_script = rs["raw"],
                        tone       = rs["tone_text"],
                        chars_init = st.session_state.get("chars_init", {}),
                    )
                    rs["segment_structure"] = result
                    _save()
                st.rerun()
        with b2:
            if ss.get("modules"):
                st.success(
                    f"✅ {ss.get('episode_type', '')} · "
                    f"{len(ss['modules'])}个叙事segment · "
                    f"{ss.get('structure_desc', '')}"
                )
                with st.expander("查看叙事结构", expanded=False):
                    for m in ss["modules"]:
                        hook_icon = (
                            "🎣" if m.get("hook_type") == "opening_hook"
                            else "🎬" if m.get("hook_type") == "ending_suspense"
                            else ""
                        )
                        st.markdown(
                            f"**{m.get('segment_id', m.get('module_id', ''))}. "
                            f"{m.get('segment_name', m.get('module_name', ''))}** {hook_icon}"
                            f" — {m.get('segment_function', m.get('module_function', ''))}"
                        )
                        for g in m.get("narrative_gaps", []):
                            st.caption(f"  ⚠️ 空白点：{g}")
            else:
                st.info("请先点击「分析叙事结构」")

    # ══════════════════════════════════════════════════════════
    # 步骤4：叙事结构问答
    # ══════════════════════════════════════════════════════════
    if rs["tone_confirmed"] and rs.get("segment_structure", {}).get("modules"):
        st.divider()
        st.markdown("### 步骤4：叙事结构问答")
        st.caption("针对叙事空白点提问，用户选择确认。随时点「完成」跳过剩余问题。")

        ss = rs["segment_structure"]

        if not rs["refine_qa"] and not rs["refine_current"] and not rs.get("refine_confirmed"):
            if st.button("▶ 开始叙事问答", type="primary", key="rs_ref_start"):
                with st.spinner("生成第一个问题..."):
                    from script_refine import next_refine_question
                    q = next_refine_question(
                        rs["raw"], rs["tone_text"], rs["refine_qa"],
                        narrative_structure=ss,
                    )
                rs["refine_current"] = q
                _save()
                st.rerun()

        for i, qa in enumerate(rs["refine_qa"]):
            answer = qa.get("custom") or qa.get("chosen_option", "")
            st.markdown(f"**Q{i+1}. {qa['question']}**")
            st.caption(f"→ {answer}")

        if rs["refine_qa"]:
            st.divider()

        cur = rs["refine_current"]
        if cur and cur.get("chosen") is None and not cur.get("custom"):
            st.markdown(f"**{cur['question']}**")
            if cur.get("which_module"):
                st.caption(f"所属segment：{cur['which_module']}")

            for oi, opt in enumerate(cur["options"]):
                if st.button(opt, key=f"rs_ref_opt_{oi}",
                             use_container_width=True):
                    rs["refine_qa"].append({
                        "question":      cur["question"],
                        "chosen_option": opt,
                        "custom":        "",
                    })
                    rs["refine_current"] = None
                    _save()
                    with st.spinner("生成下一个问题..."):
                        from script_refine import next_refine_question
                        nxt = next_refine_question(
                            rs["raw"], rs["tone_text"], rs["refine_qa"],
                            narrative_structure=ss,
                        )
                    rs["refine_current"] = nxt
                    _save()
                    st.rerun()

            bot1, bot2, bot3 = st.columns([1, 1, 2])
            with bot1:
                if st.button("🔄 换选项", key="rs_ref_refresh"):
                    with st.spinner("生成新选项..."):
                        from script_refine import refresh_options
                        new_opts = refresh_options(
                            rs["raw"], rs["tone_text"],
                            cur["question"], cur["options"],
                            rs["refine_qa"],
                        )
                    rs["refine_current"]["options"] = new_opts
                    _save()
                    st.rerun()
            with bot2:
                if st.button("🔀 换问题", key="rs_ref_regen_q"):
                    with st.spinner("重新生成问题..."):
                        from script_refine import next_refine_question
                        nxt = next_refine_question(
                            rs["raw"], rs["tone_text"], rs["refine_qa"],
                            narrative_structure=ss,
                        )
                    rs["refine_current"] = nxt
                    _save()
                    st.rerun()

            custom_input = st.text_input(
                "✏️ 自己输入答案",
                key="rs_ref_custom",
                label_visibility="collapsed",
                placeholder="以上选项都不对？直接输入你的答案...",
            )
            c1, c2 = st.columns([1, 3])
            with c1:
                if st.button("确认自定义答案", key="rs_ref_custom_ok",
                             disabled=not custom_input.strip()):
                    rs["refine_qa"].append({
                        "question":      cur["question"],
                        "chosen_option": "",
                        "custom":        custom_input.strip(),
                    })
                    rs["refine_current"] = None
                    _save()
                    with st.spinner("生成下一个问题..."):
                        from script_refine import next_refine_question
                        nxt = next_refine_question(
                            rs["raw"], rs["tone_text"], rs["refine_qa"],
                            narrative_structure=ss,
                        )
                    rs["refine_current"] = nxt
                    _save()
                    st.rerun()
            with c2:
                if st.button("完成问答 →", key="rs_ref_done", type="primary"):
                    rs["refine_confirmed"] = True
                    _save()
                    st.rerun()

        elif cur is None and not rs.get("refine_confirmed"):
            st.info("所有空白点已确认，可以进入下一步")
            if st.button("完成问答 →", type="primary", key="rs_ref_gen"):
                rs["refine_confirmed"] = True
                _save()
                st.rerun()

        if rs.get("refine_confirmed") and rs["refine_qa"]:
            st.success(f"✅ 已确认 {len(rs['refine_qa'])} 条叙事细节")
            with st.expander("查看叙事逻辑", expanded=False):
                for qa in rs["refine_qa"]:
                    answer = qa.get("custom") or qa.get("chosen_option", "")
                    st.markdown(f"• **{qa['question']}**")
                    st.caption(f"  → {answer}")
        elif rs.get("refine_confirmed"):
            st.success("✅ 跳过问答，直接使用原文和论调")

    # ══════════════════════════════════════════════════════════
    # 步骤5：按叙事结构切分原文 → segments
    # ══════════════════════════════════════════════════════════
    if rs["tone_confirmed"] and rs.get("refine_confirmed"):
        st.divider()
        st.markdown("### 步骤5：切分原文为叙事segments")
        st.caption("结合用户确认的叙事细节，按叙事结构切分原文")

        segs = rs.get("segments", [])
        d1, d2 = st.columns([1, 3])
        with d1:
            if st.button(
                "✂️ 切分原文",
                key="rs_split_segments",
                type="primary" if not segs else "secondary",
                disabled=not rs.get("segment_structure", {}).get("modules"),
            ):
                with st.spinner("按叙事结构切分中..."):
                    from script_refine import split_by_segments
                    result = split_by_segments(
                        raw_script         = rs["raw"],
                        tone               = rs["tone_text"],
                        qa_pairs           = rs["refine_qa"],
                        segment_structure  = rs["segment_structure"],
                    )
                    rs["segments"] = result
                    _save()
                st.rerun()
        with d2:
            if segs:
                st.success(f"✅ 已切分为 {len(segs)} 个叙事segment")
                
                with st.expander("查看切分结果", expanded=False):
                    for seg in segs:
                        hook_icon = (
                            "🎣" if seg.get("hook_type") == "opening_hook"
                            else "🎬" if seg.get("hook_type") == "ending_suspense"
                            else ""
                        )
                        st.markdown(
                            f"**{seg.get('segment_name', '')}** {hook_icon}"
                        )
                        st.caption(seg.get("segment_function", ""))
                        st.text(seg.get("original_text", "")[:80] + "...")
                        st.markdown("---")
            else:
                st.info("请先点击「切分原文」")

    # ══════════════════════════════════════════════════════════
    # 步骤6：逐segment漫剧化改编 → drama_segments
    # ══════════════════════════════════════════════════════════
    if rs["tone_confirmed"] and rs.get("segments"):
        st.divider()
        st.markdown("### 步骤6：漫剧化改编")
        st.caption("完善叙事，台词标准化，动作外化，为场景细分做准备（并发处理）")

        drama_segs = rs.get("drama_segments", [])
        e1, e2 = st.columns([1, 3])
        with e1:
            if st.button(
                "🎭 开始改编",
                key="rs_dramatize",
                type="primary" if not drama_segs else "secondary",
            ):
                with st.spinner(f"并发改编 {len(rs['segments'])} 个segment..."):
                    from script_refine import dramatize_all_segments
                    result = dramatize_all_segments(
                        segments          = rs["segments"],
                        tone              = rs["tone_text"],
                        chars_init        = st.session_state.get("chars_init", {}),
                        qa_pairs          = rs["refine_qa"],
                        segment_structure = rs.get("segment_structure", {}),
                    )
                    rs["drama_segments"] = result
                    _save()
                st.rerun()
        with e2:
            if drama_segs:
                st.success(f"✅ 已改编 {len(drama_segs)} 个segment")

        if drama_segs:
            with st.expander("查看改编结果（可单段重新改编）", expanded=False):
                for i, seg in enumerate(drama_segs):
                    hook_icon = (
                        "🎣" if seg.get("hook_type") == "opening_hook"
                        else "🎬" if seg.get("hook_type") == "ending_suspense"
                        else ""
                    )
                    st.markdown(
                        f"**{seg.get('segment_name', '')}** {hook_icon}"
                    )
                    st.text_area(
                        "",
                        value=seg.get("drama_text", ""),
                        height=150,
                        disabled=True,
                        key=f"drama_display_{i}",
                        label_visibility="collapsed",
                    )
                    if st.button("🔄 重新改编本段", key=f"redo_drama_{i}"):
                        with st.spinner("重新改编中..."):
                            from script_refine import dramatize_single_segment
                            prev_tail = (
                                drama_segs[i-1].get("drama_text", "")[-100:]
                                if i > 0 else ""
                            )
                            updated = dramatize_single_segment(
                                segment           = seg,
                                tone              = rs["tone_text"],
                                chars_init        = st.session_state.get("chars_init", {}),
                                qa_pairs          = rs["refine_qa"],
                                prev_tail         = prev_tail,
                                segment_structure = rs.get("segment_structure", {}),
                            )
                            rs["drama_segments"][i] = updated
                            _save()
                        st.rerun()
                    st.markdown("---")

    # ══════════════════════════════════════════════════════════
    # 步骤7：逐segment场景细分 → scenes
    # ══════════════════════════════════════════════════════════
    if rs["tone_confirmed"] and rs.get("drama_segments"):
        st.divider()
        st.markdown("### 步骤7：场景细分")
        st.caption("在每个segment内识别场景切换，每个scene是一个宫格生图单元（并发处理）")

        scenes = rs.get("scenes", [])
        f1, f2 = st.columns([1, 3])
        with f1:
            if st.button(
                "🏞️ 场景细分",
                key="rs_split_scenes",
                type="primary" if not scenes else "secondary",
            ):
                with st.spinner(f"并发细分 {len(rs['drama_segments'])} 个segment的场景..."):
                    from script_refine import split_all_scenes
                    result = split_all_scenes(
                        rs["drama_segments"],
                        chars_init = st.session_state.get("chars_init", {}),
                    )
                    rs["scenes"] = result
                    st.session_state["scenes"] = result
                    _save()
                st.rerun()
        with f2:
            if scenes:
                st.success(f"✅ 共 {len(scenes)} 个scene")

        if scenes:
            with st.expander("查看场景列表", expanded=False):
                for s in scenes:
                    hook_icon = (
                        "🎣" if s.get("hook_type") == "opening_hook"
                        else "🎬" if s.get("hook_type") == "ending_suspense"
                        else ""
                    )
                    st.markdown(
                        f"**Scene {s['scene_id']}** `{s['scene_name']}` {hook_icon} "
                        f"— {s.get('space_desc', '')}"
                    )
                    st.caption(
                        f"所属segment：{s.get('segment_name', '')} · "
                        f"{s.get('segment_function', '')}"
                    )
                    st.text(s.get("scene_text", "")[:60] + "...")
                    st.markdown("---")

        st.divider()

        if scenes:
            ok1, ok2 = st.columns([1, 3])
            with ok1:
                if st.button("✅ 确认，进入第一步切分",
                             type="primary", key="rs_confirm"):
                    rs["script_confirmed"] = True
                    st.session_state["story_tone"] = rs["tone_text"]
                    st.session_state["scenes"]     = rs["scenes"]
                    _save()
                    save_tab_snapshot(0, st.session_state, label="场景细分完成")  # ← 加这一行
                    st.rerun()
            with ok2:
                st.caption("scene细分完成后，第一步将直接对每个scene切beat")

    # ══════════════════════════════════════════════════════════
    # 完成状态
    # ══════════════════════════════════════════════════════════
    if rs.get("script_confirmed"):
        scenes = rs.get("scenes", [])
        st.success(
            f"✅ 第零步完成 · 论调：{rs['tone_text'][:40]}... · "
            f"{len(rs.get('segments', []))}个segment · "
            f"{len(scenes)}个scene"
        )
        if st.button("↺ 重新开始", key="rs_redo"):
            st.session_state["rs"] = {
                "raw":               rs["raw"],
                "tone_qa":           [],
                "tone_current":      None,
                "tone_text":         "",
                "tone_confirmed":    False,
                "segment_structure": {},
                "refine_qa":         [],
                "refine_current":    None,
                "refine_confirmed":  False,
                "segments":          [],
                "drama_segments":    [],
                "scenes":            [],
                "script_confirmed":  False,
            }
            st.rerun()
# ══════════════════════════════════════════════════════════════
# Tab1: 剧本切分
# ══════════════════════════════════════════════════════════════
with tab1:
    st.subheader("剧本切分")

    # 有scenes时用scene_text拼接，否则用refined_script
    scenes_list = st.session_state.get("scenes", [])
    if scenes_list:
        refined = "\n\n".join(
            s.get("scene_text", "") for s in scenes_list
            if s.get("scene_text")
        )
        st.info(f"✅ 将使用Tab0场景细分结果（{len(scenes_list)}个场景）跳过Segment划分")
    else:
        refined = st.session_state.get("refined_script", "")
        if refined:
            st.info("✅ 将使用精炼剧本进行切分")

    story_tone = st.session_state.get("story_tone", "")
    if story_tone:
        st.success(f"✅ 已加载故事论调：{story_tone[:60]}...")
    else:
        st.warning("⚠️ 未检测到故事论调，建议先完成第零步剧本精炼")

    raw = st.text_area(
        "剧本文本（可直接使用精炼剧本）",
        value=refined,
        height=220,
        placeholder="粘贴剧本原文，或先完成第零步精炼...",
    )

    if st.button("开始切分", type="primary", disabled=not raw.strip()):
        with st.spinner("切分中..."):
            try:
                beats, grid_map, prop_list = parse_script(
                    raw,
                    story_tone = story_tone,
                    char_names = list(st.session_state.get("chars_init", {}).keys()),
                    scenes     = st.session_state.get("scenes") or None,
                )
                from script_parser import resolve_beat_characters
                beats = resolve_beat_characters(
                    beats,
                    scenes     = st.session_state.get("scenes", []),
                    chars_init = st.session_state.get("chars_init", {}),
                )
                st.session_state["beats"]    = beats
                st.session_state["grid_map"] = grid_map

                # 道具资产初始化
                existing_props = st.session_state.get("prop_assets", {})
                for p in prop_list:
                    name = p.get("prop_name", "")
                    if name and name not in existing_props:
                        from asset_manager import init_prop_state
                        existing_props[name] = init_prop_state()
                        existing_props[name]["appear_beats"] = []
                        existing_props[name]["description"]  = p.get("reason", "")
                st.session_state["prop_assets"] = existing_props

                # 切分后清空下游状态
                for key in ["scene_assets", "shot_fields", "grid_state", "frame_state"]:
                    if key in st.session_state:
                        del st.session_state[key]

                st.success(
                    f"切分完成：{len(beats)} 个beat，"
                    f"{len(grid_map)} 个scene，"
                    f"{len(prop_list)} 个道具"
                )
            except Exception as e:
                st.error(f"切分失败：{e}")

    if "beats" in st.session_state:
        beats    = st.session_state["beats"]
        grid_map = st.session_state.get("grid_map", {})

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("总beat",    len(beats))
        c2.metric("action",   sum(1 for b in beats if b.get("type") == "action"))
        c3.metric("dialogue", sum(1 for b in beats if b.get("type") == "dialogue"))
        c4.metric("caption",  sum(1 for b in beats if b.get("type") == "caption"))

        st.download_button(
            "下载 beats JSON",
            data=json.dumps(beats, ensure_ascii=False, indent=2),
            file_name="beats.json",
            mime="application/json",
        )
        st.divider()
        line_count = sum(1 for b in beats
                 if b.get("type") in ("dialogue", "narration", "inner"))
        has_dialogues = sum(1 for b in beats if b.get("dialogues") is not None)

        dc1, dc2 = st.columns([1, 3])
        with dc1:
            if st.button(
                f"💬 提取台词 ({line_count}个line beat)",
                type="primary" if has_dialogues == 0 else "secondary",
                key="btn_extract_dialogues",
                disabled=not beats,
            ):
                with st.spinner(f"并发提取 {line_count} 个beat的台词..."):
                    from script_parser import extract_dialogues_batch
                    updated = extract_dialogues_batch(
                        st.session_state["beats"], batch_size=5)
                    st.session_state["beats"] = updated
                st.success("台词提取完成")
                st.rerun()
        with dc2:
            if has_dialogues > 0:
                filled = sum(1 for b in beats
                            if b.get("dialogues") and len(b["dialogues"]) > 0)
                st.caption(
                    f"✅ 已提取 {has_dialogues} 个beat · 有台词 {filled} 个")
            else:
                st.caption("切分完成后点击提取，获得结构化台词字段")


        st.divider()

        if grid_map:
            st.markdown("**宫格分布（以scene为单位）**")
            gdf_rows = []
            for sid in sorted(grid_map.keys()):
                g = grid_map[sid]
                seg_beats = [b for b in beats if b.get("segment") == sid]
                orig_n = sum(1 for b in seg_beats if b.get("type_flag") != "supplement")
                supp_n = sum(1 for b in seg_beats if b.get("type_flag") == "supplement")
                scene_name = seg_beats[0].get("scene_name") or seg_beats[0].get("scene", "") if seg_beats else ""
                gdf_rows.append({
                    "场景ID":   sid,
                    "场景名":   scene_name,
                    "原始beat": orig_n,
                    "补充beat": supp_n,
                    "总beat":   g["beat_count"],
                    "宫格":     f"{g['rows']}×{g['cols']}",
                })
            st.dataframe(pd.DataFrame(gdf_rows), hide_index=True)
            st.divider()

        df = pd.DataFrame([{
            "beat_id":    b.get("beat_id",""),
            "flag":       "🔵" if b.get("type_flag")=="supplement" else "⚪",
            "scene_id":   b.get("segment") or "—",
            "scene":      b.get("scene_name") or b.get("scene") or "—",
            "type":       b.get("type",""),
            "audio":      b.get("audio_type") or "—",
            "characters": " / ".join(b.get("characters") or []) or "—",
            "speaker":    b.get("speaker") or "—",
            "line":       b.get("line", "") or "—",
            "raw_text":   b.get("raw_text",""),
        } for b in beats])

        st.dataframe(df, hide_index=True, column_config={
            "beat_id":    st.column_config.TextColumn("beat",    width=80),
            "flag":       st.column_config.TextColumn("",        width=30),
            "scene_id":   st.column_config.TextColumn("场景ID",  width=60),
            "scene":      st.column_config.TextColumn("场景",    width=120),
            "type":       st.column_config.TextColumn("type",    width=55),
            "line":       st.column_config.TextColumn("台词",    width=100),
            "subtype":    st.column_config.TextColumn("subtype", width=75),
            "characters": st.column_config.TextColumn("人物",    width=120),
            "speaker":    st.column_config.TextColumn("说话人",  width=70),
            "dialogues":  st.column_config.TextColumn("台词",    width=150),
            "raw_text":   st.column_config.TextColumn("原文",    width=None),
            "audio":      st.column_config.TextColumn("音频",    width=70),
        })
        st.caption("🔵=补充分镜  ⚪=原始分镜")

        with st.expander("原始 JSON"):
            st.json(beats)
# ══════════════════════════════════════════════════════════════
# Tab2: 风格定稿
# ══════════════════════════════════════════════════════════════
with tab2:
    st.subheader("风格定稿")
    st.caption("分别为人物生图、场景生图、视频生成配置提示词后缀")

    if "style_config" not in st.session_state:
        st.session_state["style_config"] = {
            "char_suffix":  "",
            "scene_suffix": "",
            "video_suffix": "",
        }
    sc = st.session_state["style_config"]

    st.divider()
    c1, c2, c3 = st.columns(3)

    with c1:
        st.markdown("**👤 人物图风格**")
        char_suffix = st.text_area(
            "人物提示词后缀",
            value=sc.get("char_suffix", ""),
            height=120,
            key="style_char",
            placeholder="例：UE5 rendering, character design sheet, high quality CG, 50% grey background, masterpiece",
            label_visibility="collapsed",
        )

    with c2:
        st.markdown("**🏞️ 场景图风格**")
        scene_suffix = st.text_area(
            "场景提示词后缀",
            value=sc.get("scene_suffix", ""),
            height=120,
            key="style_scene",
            placeholder="例：ancient chinese architecture, cinematic lighting, concept art, masterpiece",
            label_visibility="collapsed",
        )

    with c3:
        st.markdown("**🎬 视频风格**")
        video_suffix = st.text_area(
            "视频提示词后缀",
            value=sc.get("video_suffix", ""),
            height=120,
            key="style_video",
            placeholder="例：cinematic, smooth camera movement, dramatic lighting",
            label_visibility="collapsed",
        )

    st.divider()
    if st.button("✅ 保存风格配置", type="primary", key="style_save"):
        st.session_state["style_config"] = {
            "char_suffix":  char_suffix.strip(),
            "scene_suffix": scene_suffix.strip(),
            "video_suffix": video_suffix.strip(),
            # 兼容旧suffix字段（Tab4/Tab5读suffix时兜底）
            "suffix":       char_suffix.strip(),
        }
        st.success("风格配置已保存")
        st.rerun()

    if sc.get("char_suffix") or sc.get("scene_suffix") or sc.get("video_suffix"):
        st.caption(f"👤 人物：{sc.get('char_suffix','')[:60]}")
        st.caption(f"🏞️ 场景：{sc.get('scene_suffix','')[:60]}")
        st.caption(f"🎬 视频：{sc.get('video_suffix','')[:60]}")
    
# ══════════════════════════════════════════════════════════════
# 通用抽卡渲染函数（唯一入口）
# ══════════════════════════════════════════════════════════════

def render_gacha(cfg):
    """
    通用抽卡渲染函数（纯提示词+手动上传版）。
    布局：操作行 → 提示词展示 → 上传锁定区 → 进化区
    """
    state   = cfg.state
    prompts = state.get("prompts", ["", "", ""])
    is_locked = state.get("status") == "locked"

    # ── 已锁定状态 ───────────────────────────────────────────
    if is_locked:
        lk1, lk2 = st.columns([1, 3])
        with lk1:
            st.markdown(f"**✅ {cfg.label}**")
            if state.get("locked_img"):
                st.image(state["locked_img"], width=200)
            if st.button("重新制作", key=f"{cfg.key}_redo"):
                state["status"]    = "pending"
                state["locked_img"] = None
                st.rerun()
        return

    st.markdown(f"**⬜ {cfg.label}**")

    # ── 生成提示词按钮 ────────────────────────────────────────
    op1, op2 = st.columns([1, 3])
    with op1:
        if st.button("🔄 生成提示词", key=f"{cfg.key}_gen",
                     use_container_width=True):
            with st.spinner("生成提示词..."):
                result = cfg.fn_gen_prompts()
            ps, gene, expr = (
                result if isinstance(result, tuple) else (result, "", "")
            )
            if any(ps):
                state.update({
                    "prompts":    list(ps),
                    "prompt":     ps[0],
                    "status":     "selecting",
                    "candidates": [],
                    "selected":   None,
                })
                if gene: state["gene"]       = gene
                if expr: state["expression"] = expr
                for pi in range(3):
                    st.session_state.pop(f"{cfg.key}_p{pi}", None)
            st.rerun()
    with op2:
        if any(prompts):
            st.caption("✅ 已生成提示词，复制后去外部工具生图，再上传结果")
        else:
            st.caption("先生成提示词，复制到外部工具生图")

    # ── 提示词展示（可复制文本框）────────────────────────────
    if any(prompts):
        with st.expander("📋 查看/编辑提示词", expanded=True):
            labels = ["方向1（主推）", "方向2", "方向3"]
            for pi in range(3):
                if prompts[pi]:
                    st.text_area(
                        labels[pi],
                        value=prompts[pi],
                        height=80,
                        key=f"{cfg.key}_p{pi}",
                    )
                    prompts[pi] = st.session_state.get(f"{cfg.key}_p{pi}", prompts[pi])
            state["prompts"] = prompts
            state["prompt"]  = prompts[0] if prompts else ""

        # ── 上传图片锁定 ──────────────────────────────────────
        st.markdown("**⬆️ 上传生成结果锁定**")
        uploaded = st.file_uploader(
            "上传图片",
            type=["png", "jpg", "jpeg", "webp"],
            key=f"{cfg.key}_upload",
            label_visibility="collapsed",
        )
        if uploaded is not None:
            import os, time
            os.makedirs("images", exist_ok=True)
            save_path = f"images/upload_{cfg.key}_{int(time.time())}.png"
            with open(save_path, "wb") as _f:
                _f.write(uploaded.getbuffer())
            if cfg.snapshot_type:
                from asset_manager import make_history_snapshot
                snap = make_history_snapshot(state, cfg.snapshot_type)
                state["history"].append(snap)
            state["locked_img"] = save_path
            state["status"]     = "locked"
            st.rerun()

    # ── 进化区（有history才显示）────────────────────────────
    if not cfg.snapshot_type:
        return

    history    = state.get("history", [])
    valid_hist = [h for h in history if h.get("snapshot")]
    if not valid_hist:
        return

    with st.expander(f"🧬 遗传进化（{len(valid_hist)}代历史）", expanded=False):
        c1, c2, c3 = st.columns([2, 2, 2])
        with c1:
            labels       = [f"第{h['generation']}代" for h in valid_hist]
            parent_label = st.selectbox("父代B", labels, key=f"{cfg.key}_evo_parent")
            parent_snap  = valid_hist[labels.index(parent_label)]["snapshot"]
        with c2:
            ratio = st.select_slider(
                "B代比例",
                options=[0.25, 0.50, 0.75],
                value=0.50,
                key=f"{cfg.key}_evo_ratio",
                format_func=lambda x: f"{int(x*100)}%",
            )
        with c3:
            note = st.text_input(
                "调整说明",
                key=f"{cfg.key}_evo_note",
                placeholder="可选",
                label_visibility="collapsed",
            )

        if st.button("🧬 生成进化提示词", key=f"{cfg.key}_evo_btn",
                     type="primary", use_container_width=True):
            from asset_manager import (
                make_history_snapshot,
                evolve_char, evolve_scene, evolve_prop_or_period,
            )
            snap = make_history_snapshot(state, cfg.snapshot_type)
            state["history"].append(snap)

            with st.spinner("进化中..."):
                if cfg.snapshot_type == "appearance":
                    ctx = cfg.evolve_context or {}
                    new_ps, new_ap = evolve_char(
                        char_name         = ctx.get("char_name", cfg.label),
                        char_asset        = ctx.get("char_asset", {}),
                        style_suffix      = cfg.suffix,
                        parent_b_snapshot = parent_snap,
                        ratio             = ratio,
                        user_note         = note,
                    )
                    if ctx.get("char_asset"):
                        ctx["char_asset"]["appearance"].update(new_ap)
                elif cfg.snapshot_type == "prompt":
                    ctx         = cfg.evolve_context or {}
                    gene        = ctx.get("gene", state.get("gene", ""))
                    cur_prompts = state.get("prompts", [""])
                    cur_sel     = state.get("selected", 0) or 0
                    current_p   = cur_prompts[cur_sel] if cur_sel < len(cur_prompts) else (cur_prompts[0] if cur_prompts else "")
                    if gene:
                        new_ps = evolve_scene(
                            gene=gene, current_prompt=current_p,
                            parent_b_snapshot=parent_snap,
                            style_suffix=cfg.suffix, ratio=ratio, user_note=note,
                        )
                    else:
                        new_ps = evolve_prop_or_period(
                            current_prompt=current_p,
                            parent_b_snapshot=parent_snap,
                            style_suffix=cfg.suffix, ratio=ratio, user_note=note,
                        )

            state.update({
                "prompts":    new_ps,
                "prompt":     new_ps[0],
                "generation": state.get("generation", 0) + 1,
                "status":     "selecting",
            })
            for pi in range(3):
                st.session_state.pop(f"{cfg.key}_p{pi}", None)
            st.rerun()


def _batch_buttons(label_gen, label_draw, key_gen, key_draw, configs, save_fn):
    need_gen  = [c for c in configs if not any(c.state.get("prompts", []))]
    need_regen = [c for c in configs
                  if any(c.state.get("prompts", []))
                  and c.state.get("status") != "locked"]

    b1, b2, b3 = st.columns([1, 1, 2])
    with b1:
        if st.button(label_gen, type="primary", key=key_gen, disabled=not need_gen):
            with st.spinner(f"并发生成 {len(need_gen)} 个提示词..."):
                batch_gen_prompts(need_gen)
            save_fn()
            st.rerun()
    with b2:
        if st.button("🔄 重新生成提示词", key=f"{key_gen}_regen", disabled=not need_regen):
            for c in need_regen:
                c.state["prompts"]    = []
                c.state["candidates"] = []
                c.state["selected"]   = None
            with st.spinner(f"重新生成 {len(need_regen)} 个提示词..."):
                batch_gen_prompts(need_regen)
            save_fn()
            st.rerun()
    with b3:
        st.caption(
            f"未生成:{len(need_gen)}  "
            f"未锁定:{len(need_regen)}"
        )

# ══════════════════════════════════════════════════════════════
# Tab3: 人物/场景资产
# ══════════════════════════════════════════════════════════════
with tab3:
    st.subheader("人物 / 场景 / 道具资产")

    beats        = st.session_state.get("beats", [])
    style_cfg    = st.session_state.get("style_config", {})
    char_suffix  = style_cfg.get("char_suffix", "best quality, masterpiece")
    scene_suffix = style_cfg.get("scene_suffix", "best quality, masterpiece")
    story_tone   = st.session_state.get("story_tone", "")

    if not beats:
        st.warning("请先完成第一步剧本切分")
    else:
        char_tab, period_tab, scene_tab, prop_tab = st.tabs(
            ["👤 人物主样貌", "👗 时期装扮", "🏞️ 场景", "🎭 物品道具"])

        # ══════════════════════════════════════════════════
        # 子Tab1：人物主样貌
        # ══════════════════════════════════════════════════
        with char_tab:
            chars_init      = st.session_state.get("chars_init", {})
            appearance_init = st.session_state.get("appearance_init", {})

            # Step1：确认人物列表
            if "chars" not in st.session_state:
                if chars_init:
                    st.markdown("**以下人物来自Tab0解析，可删除不需要的：**")
                    chars_text = st.text_area(
                        "人物列表", value="\n".join(chars_init.keys()),
                        height=150, key="chars_init_confirm")
                    if st.button("确认人物列表", type="primary", key="confirm_chars_init"):
                        st.session_state["chars"] = [
                            c.strip() for c in chars_text.splitlines() if c.strip()]
                        st.rerun()
                else:
                    st.warning("请先完成Tab0解析原文")

            # Step2：初始化资产
            if "chars" in st.session_state and "char_assets" not in st.session_state:
                chars_text = st.text_area(
                    "人物列表", value="\n".join(st.session_state["chars"]),
                    height=150, label_visibility="collapsed", key="chars_edit")
                if st.button("确认，初始化资产", type="primary", key="confirm_chars"):
                    chars_edited    = [c.strip() for c in chars_text.splitlines() if c.strip()]
                    char_assets_new = {}
                    for c in chars_edited:
                        asset = init_char_state()
                        ci    = chars_init.get(c, {})
                        asset["speech_style"]     = ci.get("speech_style", "")
                        asset["dialogue_samples"] = ci.get("dialogue_samples", [])
                        ap = appearance_init.get(c, {})
                        if ap:
                            asset["appearance"].update(ap)
                        char_assets_new[c] = asset
                    st.session_state["chars"]       = chars_edited
                    st.session_state["char_assets"] = char_assets_new
                    st.rerun()

            # Step3：生图
            if "char_assets" in st.session_state:
                chars       = st.session_state["chars"]
                char_assets = st.session_state["char_assets"]

                locked_n = sum(1 for c in chars
                    if char_assets.get(c,{}).get("base",{}).get("status") == "locked")
                st.caption(f"主样貌进度：{locked_n}/{len(chars)} 已锁定")
                st.progress(locked_n / len(chars) if chars else 0)

                # 用工厂函数构建configs
                base_configs = [
                    make_char_config(c, char_assets, char_suffix, story_tone, None)
                    for c in chars
                ]

                _batch_buttons(
                    "⚡ 一键生成所有提示词", "🎨 一键生成所有图片",
                    "batch_base_gen", "batch_base_draw",
                    base_configs,
                    lambda: st.session_state.__setitem__("char_assets", dict(char_assets)),
                )
                st.divider()

                for cfg in base_configs:
                    c = cfg.label
                    render_gacha(cfg)
                    st.session_state["char_assets"] = dict(char_assets)

                    # 外貌字段编辑
                    with st.expander(f"🎨 {c} 外貌字段", expanded=False):
                        ap       = char_assets[c].get("appearance", {})
                        ec1, ec2 = st.columns(2)
                        for fi, (fk, fl) in enumerate([
                            ("features","五官"), ("trait","标志特征"),
                            ("physique","体态"), ("hairstyle","发型"),
                            ("outfit","服饰"),   ("makeup","妆容"),
                        ]):
                            with (ec1 if fi % 2 == 0 else ec2):
                                nv = st.text_input(fl, value=ap.get(fk,""), key=f"ap_{c}_{fk}")
                                if nv != ap.get(fk,""):
                                    char_assets[c]["appearance"][fk] = nv
                                    st.session_state["char_assets"] = dict(char_assets)
                        st.caption(
                            f"固定：{ap.get('role_en','')}  ·  "
                            f"{ap.get('age_desc','')}  ·  {ap.get('gender','')}")

                    # 台词风格编辑
                    with st.expander(f"🗣️ {c} 台词风格", expanded=False):
                        new_s = st.text_area(
                            "台词风格", height=80,
                            value=char_assets[c].get("speech_style",""),
                            key=f"speech_style_{c}",
                            placeholder="例：句子短促，用本宫自称，擅长反问")
                        samples = char_assets[c].get("dialogue_samples",[])
                        if samples:
                            st.caption("原文样本：" + "  /  ".join(f"「{s}」" for s in samples[:3]))
                        if st.button("💾 保存", key=f"save_style_{c}"):
                            char_assets[c]["speech_style"] = new_s
                            if c in st.session_state.get("chars_init",{}):
                                st.session_state["chars_init"][c]["speech_style"] = new_s
                            st.session_state["char_assets"] = dict(char_assets)
                            st.success("已保存")

                    st.divider()

        # ══════════════════════════════════════════════════
        # 子Tab2：时期装扮
        # ══════════════════════════════════════════════════
        with period_tab:
            if "char_assets" not in st.session_state:
                st.warning("请先在「人物主样貌」完成角色初始化")
            else:
                chars       = st.session_state["chars"]
                char_assets = st.session_state["char_assets"]
                all_periods = extract_periods_from_beats(beats, chars)

                # 初始化时期状态
                for c in chars:
                    char_assets[c].setdefault("periods", {})
                    for period in all_periods.get(c, []):
                        char_assets[c]["periods"].setdefault(period, init_period_state())
                st.session_state["char_assets"] = dict(char_assets)

                period_configs = []
                for c in chars:
                    if char_assets[c].get("base",{}).get("status") != "locked":
                        continue
                    for period in char_assets[c].get("periods", {}):
                        period_configs.append(
                            make_period_config(c, period, char_assets, beats,
                                            char_suffix, story_tone, None))

                if not period_configs:
                    st.caption("请先在「人物主样貌」锁定素装图")
                else:
                    _batch_buttons(
                        "⚡ 一键生成所有时期提示词", "🎨 一键生成所有时期图片",
                        "batch_period_gen", "batch_period_draw",
                        period_configs,
                        lambda: st.session_state.__setitem__("char_assets", dict(char_assets)),
                    )
                    st.divider()
                    for cfg in period_configs:
                        render_gacha(cfg)
                        st.session_state["char_assets"] = dict(char_assets)
                        st.divider()

        # ══════════════════════════════════════════════════
        # 子Tab3：场景
        # ══════════════════════════════════════════════════
        with scene_tab:
            scenes_list = st.session_state.get("scenes", [])
            if not scenes_list:
                scenes_list = [{"scene_name": n, "space_desc":"", "segment_name":""}
                               for n in extract_scenes(beats).keys()]

            if not scenes_list:
                st.info("请先完成第零步场景细分")
            else:
                existing = st.session_state.get("scene_assets", {})
                for s in scenes_list:
                    name = s.get("scene_name","")
                    if not name: continue
                    existing.setdefault(name, init_scene_state())
                    sc = existing[name]
                    sc.setdefault("scene_card", {})
                    for k, v in [
                        ("scene_name",       name),
                        ("space_desc",       s.get("space_desc","")),
                        ("atmosphere",       s.get("space_desc","")),
                        ("dominant_emotion", s.get("dominant_emotion","")),
                        ("segment_name",     s.get("segment_name","")),
                        ("scene_id",         s.get("scene_id",0)),
                    ]:
                        sc["scene_card"].setdefault(k, v)
                st.session_state["scene_assets"] = existing
                s_assets = st.session_state["scene_assets"]

                locked_n = sum(1 for s in scenes_list
                    if s_assets.get(s.get("scene_name",""),{}).get("status") == "locked")
                st.caption(f"场景进度：{locked_n}/{len(scenes_list)} 已锁定")
                st.progress(locked_n / len(scenes_list) if scenes_list else 0)
                st.divider()

                bc1, bc2 = st.columns([1, 3])
                with bc1:
                    need = [s for s in scenes_list
                            if not any(s_assets.get(s.get("scene_name",""),{}).get("prompts",[]))
                            and s_assets.get(s.get("scene_name",""),{}).get("status") != "locked"]
                    if st.button(f"⚡ 生成所有场景提示词 ({len(need)})",
                                 type="primary" if need else "secondary",
                                 disabled=not need, use_container_width=True,
                                 key="btn_gen_scene_prompts"):
                        import threading as _t; _lock = _t.Lock()
                        def _gen(si):
                            n = si.get("scene_name","")
                            try:
                                ps, gene, expr = generate_scene_prompts_3(
                                    s_assets[n].get("scene_card",{}), scene_suffix, story_tone)
                                with _lock:
                                    s_assets[n].update({"prompts":ps,"prompt":ps[0],
                                        "gene":gene,"expression":expr,"status":"selecting"})
                            except Exception as e: print(f"[scene]{n}:{e}")
                        with st.spinner(f"生成 {len(need)} 个..."):
                            _ts = [_t.Thread(target=_gen,args=(s,),daemon=True) for s in need]
                            for t in _ts: t.start()
                            for t in _ts: t.join()
                        st.session_state["scene_assets"] = dict(s_assets); st.rerun()
                with bc2:
                    st.caption("提示词生成后，复制到外部工具生图，再在各场景卡片中上传图片锁定")

                st.divider()

                # 逐场景
                for s in scenes_list:
                    scene_name = s.get("scene_name","")
                    if not scene_name: continue
                    hook_icon = ("🎣" if s.get("hook_type")=="opening_hook"
                                 else "🎬" if s.get("hook_type")=="ending_suspense" else "")
                    st.markdown(f"### 🏞️ {scene_name} {hook_icon}")
                    st.caption(
                        f"{s.get('segment_name','')}  ·  {s.get('space_desc','')}"
                        + (f"  ·  {s.get('dominant_emotion','')}" if s.get('dominant_emotion') else ""))

                    render_gacha(make_scene_config(scene_name, s_assets, scene_suffix, story_tone, None))
                    st.session_state["scene_assets"] = dict(s_assets)
                    st.divider()

        # ══════════════════════════════════════════════════
        # 子Tab4：物品道具
        # ══════════════════════════════════════════════════
        with prop_tab:
            prop_assets = st.session_state.get("prop_assets", {})
            props_init  = st.session_state.get("props_init", [])

            for p in props_init:
                name = p.get("name","")
                if name and name not in prop_assets:
                    prop_assets[name] = init_prop_state()
                    prop_assets[name]["description"] = p.get("description","")
                    prop_assets[name]["aliases"]     = p.get("aliases",[])
            if props_init:
                st.session_state["prop_assets"] = prop_assets

            st.caption(f"共 {len(prop_assets)} 个道具" if prop_assets else "")

            with st.expander("➕ 手动添加道具", expanded=False):
                new_name = st.text_input("道具名称", key="new_prop_name")
                new_desc = st.text_input("描述（外观/材质/颜色）", key="new_prop_desc")
                if st.button("添加", key="add_prop_btn", disabled=not new_name.strip()):
                    if new_name.strip() not in prop_assets:
                        prop_assets[new_name.strip()] = init_prop_state()
                        prop_assets[new_name.strip()]["description"] = new_desc.strip()
                        st.session_state["prop_assets"] = prop_assets
                        st.rerun()

            if not prop_assets:
                st.info("Tab0解析原文后自动提取道具，或手动添加")

            st.divider()

            for prop_name, prop_state in prop_assets.items():
                for k, v in [("description",""),("aliases",[]),("prompts",["","",""]),
                              ("candidates",[]),("selected",None),("locked_img",None),
                              ("status","pending"),("gene",""),("generation",0),("history",[])]:
                    prop_state.setdefault(k, v)

                aliases = prop_state.get("aliases",[])
                st.markdown(f"**{prop_name}**"
                            + (f"（{' / '.join(aliases)}）" if aliases else ""))
                new_desc = st.text_area(
                    "描述", value=prop_state.get("description",""),
                    height=60, key=f"prop_desc_{prop_name}",
                    label_visibility="collapsed")
                if new_desc != prop_state.get("description",""):
                    prop_assets[prop_name]["description"] = new_desc
                    st.session_state["prop_assets"] = prop_assets

                render_gacha(make_prop_config(prop_name, prop_assets, char_suffix, story_tone, None))
                st.session_state["prop_assets"] = dict(prop_assets)
                st.divider()
        st.divider()
        st.markdown("**人物视觉代称**")
        st.caption("锁定人物资产后生成，用于宫格图人物一致性控制")
        aliases = st.session_state.get("visual_aliases", {})
        if aliases:
            for _name, _alias in aliases.items():
                st.caption(f"**{_name}** → `{_alias}`")
        if st.button("🏷️ 生成人物视觉代称", key="gen_visual_aliases"):
            from asset_manager import generate_visual_aliases
            with st.spinner("分析人物外貌，生成唯一视觉代称..."):
                _result = generate_visual_aliases(
                    chars_init      = st.session_state.get("chars_init", {}),
                    appearance_init = st.session_state.get("appearance_init", {}),
                )
            if _result:
                st.session_state["visual_aliases"] = _result
                st.success(f"✅ 已生成 {len(_result)} 个视觉代称")
                st.rerun()
            else:
                st.error("生成失败，请重试")


# ══════════════════════════════════════════════════════════════
# Tab4：图像字段扩写（纯文字，不生图）
# ══════════════════════════════════════════════════════════════
with tab4:
    st.subheader("图像字段扩写")
    st.caption("为每个beat确定景别/机位/光线/情绪/主体描述，作为Tab5宫格生图的骨架")

    beats        = st.session_state.get("beats", [])
    style_cfg    = st.session_state.get("style_config", {})
    suffix       = style_cfg.get("char_suffix", "best quality, masterpiece")
    char_assets  = st.session_state.get("char_assets", {})
    scene_assets = st.session_state.get("scene_assets", {})
    story_tone   = st.session_state.get("story_tone", "")
    refined_script = st.session_state.get("refined_script", "")

    if not beats:
        st.warning("请先完成第一步剧本切分")
    else:
        if "shot_fields" not in st.session_state:
            st.session_state["shot_fields"] = {}
        sf = st.session_state["shot_fields"]

        for b in beats:
            bid = b["beat_id"]
            if bid not in sf:
                sf[bid] = {"status": "pending"}

        shot_beats = [b for b in beats if b.get("type") != "caption"]
        skip_beats = [b for b in beats if b.get("type") == "caption"]
        done_n  = sum(1 for b in shot_beats
                      if sf.get(b["beat_id"], {}).get("status") in ("done", "edited"))
        total_n = len(shot_beats)

        st.caption(f"共 {total_n} 个分镜  |  已扩写 {done_n}  |  caption跳过 {len(skip_beats)}")
        st.progress(done_n / total_n if total_n else 0)
        st.divider()

        ba1, ba2, ba3 = st.columns([1, 1, 2])
        with ba1:
            if st.button("⚡ 一键扩写全部", type="primary", key="sf_batch_all"):
                with st.spinner("四层架构扩写中（全局预读→叙事分析→景别规划→逐beat执行）..."):
                    from shot_optimizer import run_all
                    results = run_all(
                        beats, char_assets, scene_assets,
                        suffix, story_tone, refined_script,
                        chars_init=st.session_state.get("chars_init", {}),
                    )
                for bid, fields in results.items():
                    sf[bid] = fields
                    st.session_state.pop(f"sf_edit_open_{bid}", None)
                st.session_state["shot_fields"] = dict(sf)
                st.rerun()

        with ba2:
            pending_n = total_n - done_n
            st.caption(f"待扩写：{pending_n} 个")

        with ba3:
            st.caption("或按段落逐步处理 ↓（单段扩写带上下文衔接，质量更高）")

        st.divider()

        segments: dict = {}
        for b in beats:
            seg = b.get("segment", 0)
            segments.setdefault(seg, []).append(b)
        total_segs = len(segments)

        for seg_id, seg_beats in sorted(segments.items()):
            seg_desc = seg_beats[0].get("segment_desc", "")
            seg_shot = [b for b in seg_beats if b.get("type") != "caption"]
            seg_done = sum(1 for b in seg_shot
                           if sf.get(b["beat_id"], {}).get("status") in ("done", "edited"))

            from shot_optimizer import infer_narrative_phase
            phase = infer_narrative_phase(seg_id, total_segs)

            with st.expander(
                f"Segment {seg_id}  ·  {seg_desc}  ·  [{seg_done}/{len(seg_shot)} 已扩写]",
                expanded=(seg_done < len(seg_shot))
            ):
                st.caption(f"📍 {phase}")

                sc1, sc2 = st.columns([1, 3])
                with sc1:
                    if st.button(f"扩写本段", key=f"sf_seg_{seg_id}", type="primary"):
                        with st.spinner(f"扩写 Segment {seg_id}（三层串行）..."):
                            from shot_optimizer import run_segment
                            results = run_segment(
                                seg_id, beats,
                                char_assets, scene_assets,
                                suffix, story_tone, refined_script,
                                existing_shot_fields=sf,
                                chars_init=st.session_state.get("chars_init", {}),
                            )
                        for bid, fields in results.items():
                            sf[bid] = fields
                            st.session_state.pop(f"sf_edit_open_{bid}", None)
                        st.session_state["shot_fields"] = dict(sf)
                        st.rerun()
                with sc2:
                    from shot_optimizer import missing_assets
                    missing = set()
                    for b in seg_shot:
                        for c in missing_assets(b, char_assets, scene_assets):
                            missing.add(c)
                    if missing:
                        st.caption(f"⚠️ 无立绘（不影响扩写）：{', '.join(missing)}")

                st.divider()

                for b in seg_beats:
                    bid   = b["beat_id"]
                    btype = b.get("type", "")
                    bsub  = b.get("subtype", "")
                    state = sf.get(bid, {"status": "pending"})

                    if btype == "caption":
                        st.caption(f"📝 {bid} [caption]  {b.get('raw_text','')[:50]}")
                        continue

                    status = state.get("status", "pending")
                    icon   = {"done": "✅", "edited": "✏️", "pending": "⬜"}.get(status, "⬜")

                    chars_str = "  ".join(b.get("characters") or []) or ""
                    scene_str = b.get("scene_name") or b.get("scene") or ""
                    st.markdown(
                        f"**{bid}** `{btype}` {icon}  "
                        f"{chars_str}  `{scene_str}`"
                    )
                    st.caption(b.get("raw_text", ""))

                    if status in ("done", "edited"):
                        # 骨架字段
                        st.markdown(
                            f"`{state.get('shot_scale','')}` "
                            f"`{state.get('camera_angle','')}` "
                            f"`{state.get('lighting','')}` "
                            f"`{state.get('mood','')}` "
                        )
                        # 新字段行
                        new_field_parts = []
                        if state.get("audio_type"):
                            new_field_parts.append(f"🔊 {state['audio_type']}")
                        if state.get("energy_type"):
                            new_field_parts.append(f"⚡ {state['energy_type']}")
                        if state.get("motion_vector"):
                            new_field_parts.append(f"→ {state['motion_vector']}")
                        if state.get("duration_hint"):
                            new_field_parts.append(f"⏱ {state['duration_hint']}s")
                        if new_field_parts:
                            st.caption("  ".join(new_field_parts))
                        # 台词
                        if state.get("dialogue"):
                            emotion = f"（{state['dialogue_emotion']}）" if state.get("dialogue_emotion") else ""
                            st.caption(f"💬 {state.get('speaker','')} {emotion}：{state['dialogue']}")
                        if state.get("rationale"):
                            st.caption(f"💡 {state['rationale']}")
                        if state.get("reasoning"):
                            st.caption(f"🔍 {state['reasoning']}")

                        checks = state.get("step3_checks", {})
                        if checks:
                            check_str = "  ".join(
                                f"{k[:4]}:{v[:1]}" for k, v in checks.items()
                            )
                            st.caption(f"铁律自检：{check_str}")

                        if state.get("subject"):
                            st.text_area(
                                "subject",
                                value=state.get("subject", ""),
                                height=60,
                                key=f"sf_sub_display_{bid}",
                                disabled=True,
                                label_visibility="collapsed",
                            )

                        op1, op2 = st.columns([1, 1])
                        with op1:
                            if st.button("🔄 单独重扩", key=f"sf_redo_{bid}"):
                                with st.spinner("重新扩写..."):
                                    from shot_optimizer import run_single_beat
                                    result = run_single_beat(
                                        b, sf,
                                        char_assets, scene_assets,
                                        suffix, story_tone, refined_script,
                                        chars_init=st.session_state.get("chars_init", {}),
                                    )
                                sf[bid] = result
                                st.session_state.pop(f"sf_edit_open_{bid}", None)
                                st.session_state["shot_fields"] = dict(sf)
                                st.rerun()

                        with op2:
                            edit_key  = f"sf_edit_open_{bid}"
                            is_open   = st.session_state.get(edit_key, False)
                            btn_label = "收起 ▲" if is_open else "✏️ 编辑 ▼"
                            if st.button(btn_label, key=f"sf_edit_btn_{bid}"):
                                st.session_state[edit_key] = not is_open
                                st.rerun()

                        if st.session_state.get(f"sf_edit_open_{bid}", False):
                            st.markdown("---")

                            scale_opts = ["ELS","LS","MS","MCU","CU","ECU"]
                            angle_opts = ["eye_level","low_angle","high_angle","dutch"]
                            light_opts = ["soft_natural","dramatic_side","backlight",
                                          "candlelight","cold_fluorescent","warm_indoor"]

                            e1, e2 = st.columns(2)
                            with e1:
                                cur_scale = state.get("shot_scale", "MS")
                                new_scale = st.selectbox(
                                    "景别", scale_opts,
                                    index=scale_opts.index(cur_scale) if cur_scale in scale_opts else 2,
                                    key=f"sf_scale_{bid}"
                                )
                                cur_angle = state.get("camera_angle", "eye_level")
                                new_angle = st.selectbox(
                                    "机位", angle_opts,
                                    index=angle_opts.index(cur_angle) if cur_angle in angle_opts else 0,
                                    key=f"sf_angle_{bid}"
                                )
                            with e2:
                                cur_light = state.get("lighting", "soft_natural")
                                new_lighting = st.selectbox(
                                    "光线", light_opts,
                                    index=light_opts.index(cur_light) if cur_light in light_opts else 0,
                                    key=f"sf_light_{bid}"
                                )
                                new_mood = st.text_input(
                                    "情绪", value=state.get("mood", ""),
                                    key=f"sf_mood_{bid}"
                                )

                            new_subject = st.text_area(
                                "主体描述（subject）",
                                value=state.get("subject", ""),
                                height=80,
                                key=f"sf_subject_{bid}"
                            )
                            new_rationale = st.text_input(
                                "导演意图",
                                value=state.get("rationale", ""),
                                key=f"sf_rationale_{bid}"
                            )

                            if st.button("💾 保存修改", key=f"sf_save_{bid}", type="primary"):
                                sf[bid].update({
                                    "shot_scale":   new_scale,
                                    "camera_angle": new_angle,
                                    "lighting":     new_lighting,
                                    "mood":         new_mood,
                                    "subject":      new_subject,
                                    "rationale":    new_rationale,
                                    "status":       "edited",
                                })
                                st.session_state[f"sf_edit_open_{bid}"] = False
                                st.session_state["shot_fields"] = dict(sf)
                                st.rerun()

                            st.markdown("---")

                    else:
                        if st.button("单独扩写", key=f"sf_one_{bid}"):
                            with st.spinner("扩写中..."):
                                from shot_optimizer import run_single_beat
                                result = run_single_beat(
                                    b, sf,
                                    char_assets, scene_assets,
                                    suffix, story_tone, refined_script,
                                    chars_init=st.session_state.get("chars_init", {}),
                                )
                            sf[bid] = result
                            st.session_state["shot_fields"] = dict(sf)
                            st.rerun()

                    st.divider()


# ══════════════════════════════════════════════════════════════
# Tab5：宫格生图与遗传（无嵌套expander版本）
# ══════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════
# Tab5：宫格生图与遗传（无嵌套expander版本）
# ══════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════
# Tab5：宫格生图与遗传（无嵌套expander版本）
# ══════════════════════════════════════════════════════════════

with tab5:
    st.subheader("宫格生图与遗传")
    st.caption("以segment为单位生成宫格图，遗传优化后锁定，PIL切分得到每帧")

    beats        = st.session_state.get("beats", [])
    shot_fields  = st.session_state.get("shot_fields", {})
    grid_map     = st.session_state.get("grid_map", {})
    style_cfg    = st.session_state.get("style_config", {})
    suffix       = style_cfg.get("char_suffix", "best quality, masterpiece")
    char_assets    = st.session_state.get("char_assets", {})
    scene_assets   = st.session_state.get("scene_assets", {})
    visual_aliases = st.session_state.get("visual_aliases", {})

    if not beats:
        st.warning("请先完成第一步剧本切分")
    elif not shot_fields or not any(
        v.get("status") in ("done", "edited")
        for v in shot_fields.values()
        if isinstance(v, dict)
    ):
        st.warning("请先完成第四步图像字段扩写")
    else:
        from grid_generator import (
            init_grid_state, build_panel_descs, evolve_panel_descs,
            assemble_grid_prompt, collect_ref_images, split_grid,
        )
        # generate_shot_images 通过 app.py 的 _gsi 封装调用，读取侧边栏模型设置
        from shot_optimizer import infer_narrative_phase
        from grid_logger import write_log, read_logs, get_log_path
        import threading as _t

        # ── 初始化 grid_state ────────────────────────────────
        if "grid_state" not in st.session_state:
            st.session_state["grid_state"] = {}
        gs = st.session_state["grid_state"]

        segments: dict = {}
        for b in beats:
            sid = b.get("segment", 0)
            segments.setdefault(sid, []).append(b)
        total_segs = len(segments)

        for sid, seg_beats in segments.items():
            if sid not in gs:
                gs[sid] = init_grid_state(sid, seg_beats, shot_fields, grid_map)

        # 旧存档兼容：确保所有字段存在（即使没有调用_patch_grid_state也能正常运行）
        import copy as _copy
        _GRID_DEFAULTS = {
            "beat_ids": [], "panel_descs": [], "grid_prompt": "",
            "candidates": [], "selected": None, "selected_cells": [],
            "locked_img": None, "locked_cells": {}, "generation": 0,
            "history": [], "status": "pending", "ref_notes": "", "l1_summary": "","char_ref_map": {},
        }
        for sid, seg_beats in segments.items():
            g = gs[sid]
            for _k, _v in _GRID_DEFAULTS.items():
                if _k not in g:
                    g[_k] = _copy.deepcopy(_v)
            # beat_ids为空时重新计算
            if not g["beat_ids"]:
                g["beat_ids"] = [
                    b["beat_id"] for b in seg_beats
                    if b.get("type") != "caption"
                ]
            # rows/cols为0时重新计算
            if not g.get("rows") or not g.get("cols"):
                from grid_generator import get_grid_shape_for_beats
                g["rows"], g["cols"] = get_grid_shape_for_beats(len(g["beat_ids"]))
                g["total"] = g["rows"] * g["cols"]

        st.session_state["grid_state"] = gs

        # ── 进度统计 ─────────────────────────────────────────
        has_descs_n = sum(1 for v in gs.values() if v.get("panel_descs"))
        has_cands_n = sum(1 for v in gs.values() if any(v.get("candidates", [])))
        locked_n    = sum(1 for v in gs.values() if v.get("status") == "locked")

        p1, p2, p3, p4 = st.columns(4)
        p1.metric("总scene", total_segs)
        p2.metric("已生成描述", has_descs_n)
        p3.metric("已生成图", has_cands_n)
        p4.metric("已锁定", locked_n)
        st.progress(locked_n / total_segs if total_segs else 0)

        # 日志查看入口
        log_col1, log_col2 = st.columns([1, 4])
        with log_col1:
            log_key = "gs_show_log"
            log_open = st.session_state.get(log_key, False)
            if st.button(
                "收起日志 ▲" if log_open else "📋 查看生图日志",
                key="gs_log_btn",
            ):
                st.session_state[log_key] = not log_open
                st.rerun()
        with log_col2:
            st.caption(f"日志路径：logs/grid_log.json")
        if st.session_state.get("gs_show_log", False):
            log_records = read_logs()
            if not log_records:
                st.caption("暂无日志")
            else:
                for rec in log_records[:10]:  # 最新10条
                    action_label = {
                        "gen_first": "🎨 首次生图",
                        "evolve":    "🧬 格子进化",
                        "regen_all": "🔄 整体重生",
                    }.get(rec.get("action",""), rec.get("action",""))
                    with st.container():
                        st.caption(
                            f"{rec.get('timestamp','')}  "
                            f"Seg{rec.get('seg_id','')}  "
                            f"第{rec.get('generation','')}代  "
                            f"{action_label}  "
                            f"成功{rec.get('success_count',0)}/3张  "
                            f"{rec.get('rows','')}×{rec.get('cols','')}"
                        )
                        pt_lkey = f"gs_log_pt_{rec.get('timestamp','')}_{rec.get('seg_id','')}_{rec.get('generation','')}"
                        if st.button("查看提示词", key=pt_lkey):
                            st.session_state[pt_lkey + "_open"] = not st.session_state.get(pt_lkey + "_open", False)
                            st.rerun()
                        if st.session_state.get(pt_lkey + "_open", False):
                            st.text_area(
                                "完整宫格提示词",
                                value=rec.get("grid_prompt",""),
                                height=120,
                                disabled=True,
                                key=f"gs_log_pt_area_{pt_lkey}",
                            )
                            # 逐Panel描述
                            for pd in rec.get("panel_descs", []):
                                st.caption(f"Panel {pd.get('panel_num','')} ({pd.get('beat_id','')}): {pd.get('desc','')}")
                        st.markdown("---")
        st.divider()

        # ══════════════════════════════════════════════════════
        # 三个批量按钮
        # ══════════════════════════════════════════════════════
        st.markdown("**批量操作**")

        # ── 第一行：描述生成 ──────────────────────────────
        bd1, bd2, bd3 = st.columns([1, 1, 2])

        with bd1:
            need_descs = [
                int(sid) for sid, g in gs.items()
                if not g.get("panel_descs")
                and g.get("status") != "locked"
                and all(
                    shot_fields.get(b["beat_id"], {}).get("status") in ("done", "edited")
                    for b in segments.get(sid, [])
                    if b.get("type") != "caption"
                )
            ]
            if st.button(
                f"① 生成描述 ({len(need_descs)})",
                type="primary" if need_descs else "secondary",
                disabled=not need_descs,
                use_container_width=True,
                key="gs_batch_descs",
            ):
                _lock = _t.Lock()
                def _gen_descs(sid):
                    seg_beats = segments[sid]
                    g         = gs[sid]
                    phase     = infer_narrative_phase(sid, total_segs)
                    try:
                        _, _, ref_notes, char_ref_map = collect_ref_images(
                            seg_beats, char_assets, scene_assets,
                            visual_aliases=visual_aliases,
                        )
                        panel_descs = build_panel_descs(
                            sid, seg_beats, shot_fields,
                            ref_notes, suffix,
                            g.get("l1_summary", ""), phase,
                            rows=g["rows"], cols=g["cols"],
                            char_ref_map=char_ref_map,
                        )
                        grid_prompt = assemble_grid_prompt(
                            panel_descs, g["rows"], g["cols"],
                            ref_notes, suffix, g.get("l1_summary", ""),
                        )
                        with _lock:
                            g["panel_descs"]  = panel_descs
                            g["ref_notes"]    = ref_notes
                            g["grid_prompt"]  = grid_prompt
                            g["char_ref_map"] = char_ref_map
                    except Exception as e:
                        print(f"[tab5] gen_descs seg{sid}: {e}")

                with st.spinner(f"生成 {len(need_descs)} 个描述..."):
                    _ts = [_t.Thread(target=_gen_descs, args=(sid,), daemon=True)
                           for sid in need_descs]
                    for t in _ts: t.start()
                    for t in _ts: t.join()
                st.session_state["grid_state"] = dict(gs)
                st.rerun()

        with bd2:
            need_regen_descs = [
                int(sid) for sid, g in gs.items()
                if g.get("panel_descs")
                and g.get("status") != "locked"
            ]
            if st.button(
                f"🔄 重新生成描述 ({len(need_regen_descs)})",
                disabled=not need_regen_descs,
                use_container_width=True,
                key="gs_batch_regen_descs",
            ):
                _lock = _t.Lock()
                def _regen_descs(sid):
                    seg_beats = segments[sid]
                    g         = gs[sid]
                    phase     = infer_narrative_phase(sid, total_segs)
                    try:
                        _, _, ref_notes, char_ref_map = collect_ref_images(
                            seg_beats, char_assets, scene_assets,
                            visual_aliases=visual_aliases,
                        )
                        panel_descs = build_panel_descs(
                            sid, seg_beats, shot_fields,
                            ref_notes, suffix,
                            g.get("l1_summary", ""), phase,
                            rows=g["rows"], cols=g["cols"],
                            char_ref_map=char_ref_map,
                        )
                        grid_prompt = assemble_grid_prompt(
                            panel_descs, g["rows"], g["cols"],
                            ref_notes, suffix, g.get("l1_summary", ""),
                        )
                        with _lock:
                            g["panel_descs"]  = panel_descs
                            g["ref_notes"]    = ref_notes
                            g["grid_prompt"]  = grid_prompt
                            g["char_ref_map"] = char_ref_map
                    except Exception as e:
                        print(f"[tab5] regen_descs seg{sid}: {e}")

                with st.spinner(f"重新生成 {len(need_regen_descs)} 个描述..."):
                    _ts = [_t.Thread(target=_regen_descs, args=(sid,), daemon=True)
                           for sid in need_regen_descs]
                    for t in _ts: t.start()
                    for t in _ts: t.join()
                st.session_state["grid_state"] = dict(gs)
                st.rerun()

        with bd3:
            desc_done  = sum(1 for g in gs.values() if g.get("panel_descs"))
            desc_lock  = sum(1 for g in gs.values() if g.get("status") == "locked")
            st.caption(f"描述：{desc_done}/{total_segs} 已生成  |  锁定：{desc_lock}/{total_segs}")

        # ── 第二行：批量切分统计 ──────────────────────────
        bi1, bi2 = st.columns([1, 3])
        with bi1:
            need_split = [
                int(sid) for sid, g in gs.items()
                if g.get("candidates")
                and g.get("selected") is not None
                and g.get("status") != "locked"
            ]
            if st.button(
                f"② 批量切分锁定 ({len(need_split)})",
                disabled=not need_split,
                use_container_width=True,
                key="gs_batch_split",
            ):
                import threading as _t
                _lock = _t.Lock()
                def _do_split(sid):
                    g = gs[sid]
                    sel_img = g["candidates"][g["selected"]]
                    try:
                        cells = split_grid(sel_img, g["rows"], g["cols"], seg_id=sid)
                        with _lock:
                            g["locked_img"]   = sel_img
                            g["locked_cells"] = cells
                            g["status"]       = "locked"
                    except Exception as e:
                        print(f"[tab5] split seg{sid}: {e}")
                with st.spinner(f"切分锁定 {len(need_split)} 个..."):
                    _ts = [_t.Thread(target=_do_split, args=(sid,), daemon=True)
                           for sid in need_split]
                    for t in _ts: t.start()
                    for t in _ts: t.join()
                st.session_state["grid_state"] = dict(gs)
                st.rerun()
        with bi2:
            uploaded_n = sum(1 for g in gs.values() if any(g.get("candidates", [])))
            locked_n   = sum(1 for g in gs.values() if g.get("status") == "locked")
            st.caption(f"已上传宫格图：{uploaded_n}/{total_segs}  |  已锁定：{locked_n}/{total_segs}")

        st.divider()

        # ══════════════════════════════════════════════════════
        # 逐 segment 详细操作
        # ══════════════════════════════════════════════════════
        for seg_id, seg_beats in sorted(segments.items()):
            g        = gs[seg_id]
            seg_desc = seg_beats[0].get("segment_desc", "")
            rows     = g["rows"]
            cols     = g["cols"]
            status   = g.get("status", "pending")
            phase    = infer_narrative_phase(seg_id, total_segs)
            icon     = {"locked": "🔒", "selecting": "🎨", "pending": "⬜"}.get(status, "⬜")

            # 锁定状态也默认展开，让用户能看到切分结果
            _exp_open = True  # 始终展开，让用户看到上传框和操作按钮
            scene_name = (
                seg_beats[0].get("scene_name") or seg_beats[0].get("scene", seg_desc)
                if seg_beats else seg_desc
            )
            with st.expander(
                f"Scene {seg_id}  ·  {scene_name}  ·  {rows}×{cols}  {icon}",
                expanded=_exp_open,
            ):
                # ── 已锁定 ───────────────────────────────────
                if status == "locked":
                    st.success("✅ 已锁定")
                    if g.get("locked_img"):
                        st.image(g["locked_img"], caption="锁定宫格图", width=400)

                    # locked_cells key可能是字符串（JSON加载），统一转整数
                    locked_cells = {int(k): v for k, v in g.get("locked_cells", {}).items()}
                    beat_ids     = g.get("beat_ids", [])

                    if locked_cells:
                        st.markdown("**切分结果（逐帧）**")
                        cell_nums = sorted(locked_cells.keys())
                        for row_start in range(0, len(cell_nums), cols):
                            row_cells = cell_nums[row_start:row_start + cols]
                            c_cols    = st.columns(len(row_cells))
                            for ci, cn in enumerate(row_cells):
                                bid = beat_ids[cn - 1] if cn - 1 < len(beat_ids) else ""
                                cell_path = locked_cells[cn]
                                import os
                                if cell_path and os.path.exists(str(cell_path)):
                                    c_cols[ci].image(cell_path, caption=f"格{cn}  {bid}", width=150)
                                else:
                                    c_cols[ci].caption(f"格{cn} 图片缺失")
                    else:
                        # 有锁定图但没有切分结果（可能是旧存档或切分失败）
                        st.warning("未找到切分结果，点下方按钮重新切分")

                    # 操作按钮行
                    op1, op2 = st.columns([1, 1])
                    with op1:
                        if st.button("✂️ 重新切分", key=f"gs_resplit_{seg_id}"):
                            if g.get("locked_img"):
                                import os
                                if os.path.exists(str(g["locked_img"])):
                                    with st.spinner("切分中..."):
                                        try:
                                            cells = split_grid(g["locked_img"], rows, cols, seg_id=seg_id)
                                            g["locked_cells"] = cells
                                            st.session_state["grid_state"] = dict(gs)
                                            st.rerun()
                                        except Exception as e:
                                            st.error(f"切分失败：{e}")
                                else:
                                    st.error("锁定图路径不存在，请重新制作")
                            else:
                                st.error("没有锁定图，请先锁定")
                    with op2:
                        if st.button("↺ 重新制作", key=f"gs_redo_{seg_id}"):
                            g.update({
                                "status": "pending", "candidates": [],
                                "selected": None, "locked_img": None,
                                "locked_cells": {},
                            })
                            st.session_state["grid_state"] = dict(gs)
                            st.rerun()
                    continue

                # ── 字段检查 ──────────────────────────────────
                valid_beats = [b for b in seg_beats
                                if b.get("type") != "caption"]
                has_fields  = all(
                    shot_fields.get(b["beat_id"], {}).get("status") in ("done", "edited")
                    for b in valid_beats
                )
                if not has_fields:
                    st.warning("本段部分beat尚未扩写，请先完成第四步")
                    continue

                # ── Panel描述展示（session_state控制，避免嵌套expander）──
                if g.get("panel_descs"):
                    desc_key = f"gs_show_descs_{seg_id}"
                    is_open  = st.session_state.get(desc_key, False)
                    if st.button(
                        "收起Panel描述 ▲" if is_open else "📋 查看Panel描述 ▼",
                        key=f"gs_desc_btn_{seg_id}",
                    ):
                        st.session_state[desc_key] = not is_open
                        st.rerun()
                    if st.session_state.get(desc_key, False):
                        st.markdown("---")
                        for d in g["panel_descs"]:
                            evo = d.get("evolution_note", "")
                            st.caption(
                                f"Panel {d['panel_num']}  {d['beat_id']}"
                                + (f"  ← {evo}" if evo else "")
                            )
                            st.text(d["desc"])
                        st.markdown("---")

                # ── 描述生成按钮 + 提示词查看 ─────────────────
                gen_col, prompt_col = st.columns([1, 2])
                with gen_col:
                    gen_label = (
                        "📋 生成Panel描述"
                        if not g.get("panel_descs")
                        else "🔄 重新生成描述"
                    )
                    if st.button(gen_label, key=f"gs_gen_{seg_id}", type="primary"):
                        with st.spinner("生成Panel描述..."):
                            _, _, ref_notes, char_ref_map = collect_ref_images(
                                seg_beats, char_assets, scene_assets,
                                visual_aliases=visual_aliases,
                            )
                            g["ref_notes"] = ref_notes
                            panel_descs = build_panel_descs(
                                seg_id, seg_beats, shot_fields,
                                ref_notes, suffix,
                                g.get("l1_summary", ""), phase,
                                rows=rows, cols=cols,
                                char_ref_map=char_ref_map,
                            )
                            g["panel_descs"]  = panel_descs
                            g["char_ref_map"] = char_ref_map
                            grid_prompt = assemble_grid_prompt(
                                panel_descs, rows, cols,
                                ref_notes, suffix, g.get("l1_summary", ""),
                            )
                            g["grid_prompt"] = grid_prompt
                        st.session_state["grid_state"] = dict(gs)
                        st.rerun()

                with prompt_col:
                    if g.get("grid_prompt"):
                        pt_key  = f"gs_show_pt_{seg_id}"
                        pt_open = st.session_state.get(pt_key, False)
                        if st.button(
                            "收起提示词 ▲" if pt_open else "查看宫格提示词 ▼",
                            key=f"gs_pt_btn_{seg_id}",
                        ):
                            st.session_state[pt_key] = not pt_open
                            st.rerun()
                        if st.session_state.get(pt_key, False):
                            st.text_area(
                                "", value=g["grid_prompt"], height=100,
                                disabled=True, label_visibility="collapsed",
                                key=f"gs_pt_{seg_id}",
                            )

                # ── 上传宫格图 ────────────────────────────────
                if g.get("panel_descs"):
                    st.markdown("**⬆️ 上传宫格图（外部生图后上传）**")
                    st.caption(f"请按 {rows}×{cols} 宫格生图后上传整张图，系统自动切分")
                    uploaded = st.file_uploader(
                        "上传宫格图",
                        type=["png", "jpg", "jpeg", "webp"],
                        key=f"gs_upload_{seg_id}_{g.get('generation',0)}",
                        label_visibility="collapsed",
                    )
                    if uploaded is not None:
                        import os, time
                        os.makedirs("images", exist_ok=True)
                        save_path = f"images/grid_{seg_id}_{int(time.time())}.png"
                        with open(save_path, "wb") as _f:
                            _f.write(uploaded.getbuffer())
                        g["candidates"] = [save_path, None, None]
                        g["selected"]   = 0
                        g["generation"] = g.get("generation", 0) + 1
                        g["status"]     = "selecting"
                        st.session_state["grid_state"] = dict(gs)
                        st.rerun()
                else:
                    st.caption("请先生成Panel描述，再复制提示词到外部工具生图")
                    continue

                # 无图则跳过后续
                candidates = g.get("candidates", [])
                if not any(u for u in candidates if u):
                    continue

                sel_img = candidates[0]
                g["selected"] = 0
                st.divider()

                # ── 格子勾选 ──────────────────────────────────
                st.markdown("**勾选不满意的格子，然后点进化**")
                st.caption(
                    f"编号从左到右从上到下  "
                    f"| 第1行: 1~{cols}  第2行: {cols+1}~{2*cols} ..."
                )

                thumb_col, check_col = st.columns([1, 2])
                with thumb_col:
                    st.image(sel_img, width=200, caption="已选图")

                with check_col:
                    selected_cells = list(g.get("selected_cells", []))
                    new_selected   = []
                    for r in range(rows):
                        row_checks = st.columns(cols)
                        for c in range(cols):
                            cell_num = r * cols + c + 1
                            if cell_num > len(g.get("beat_ids", [])):
                                break
                            bid = (g["beat_ids"][cell_num - 1]
                                   if cell_num - 1 < len(g["beat_ids"]) else "")
                            checked = st.checkbox(
                                f"格{cell_num}  {bid}",
                                value=(cell_num in selected_cells),
                                key=f"gs_cell_{seg_id}_{cell_num}",
                            )
                            if checked:
                                new_selected.append(cell_num)

                if new_selected != selected_cells:
                    g["selected_cells"] = new_selected
                    st.session_state["grid_state"] = dict(gs)

                # ── 进化 + 锁定 ───────────────────────────────
                note_col, evo_col, lock_col = st.columns([3, 1, 1])
                with note_col:
                    user_note = st.text_input(
                        "进化说明",
                        key=f"gs_note_{seg_id}",
                        label_visibility="collapsed",
                        placeholder="例如：格子3张力不够，格子5视线方向不对...",
                    )
                with evo_col:
                    st.caption("修改描述后重新上传图片即可")

                with lock_col:
                    if st.button(
                        "✅ 锁定切分",
                        key=f"gs_lock_{seg_id}",
                        type="primary",
                        use_container_width=True,
                    ):
                        g["locked_img"] = sel_img
                        g["status"]     = "locked"
                        with st.spinner("切分宫格图..."):
                            try:
                                g["locked_cells"] = split_grid(sel_img, rows, cols, seg_id=seg_id)
                            except Exception as e:
                                st.error(f"切分失败：{e}")
                                g["locked_cells"] = {}
                        st.session_state["grid_state"] = dict(gs)
                        st.rerun()

                

with tab6:
    st.subheader("帧图尺寸转换")
    st.caption("将宫格切分帧图转换为视频生成所需尺寸")

    grid_state  = st.session_state.get("grid_state", {})
    locked_segs = {sid: g for sid, g in grid_state.items()
                   if g.get("status") == "locked" and g.get("locked_cells")}

    if not locked_segs:
        st.warning("请先在第五步完成宫格切分并锁定")
    else:
        # ── 模式切换 ──────────────────────────────────────────
        mode = "ComfyUI 两步处理"
        st.caption("转换模式：ComfyUI 两步处理（4x放大 → 尺寸转换）")

        # ── 统计帧图 ──────────────────────────────────────────
        all_cells = []
        for sid, g in sorted(locked_segs.items()):
            locked_cells = {int(k): v for k, v in g.get("locked_cells", {}).items()}
            for cn, img_path in sorted(locked_cells.items()):
                all_cells.append((sid, cn, img_path))

        total_n = len(all_cells)

        # ══════════════════════════════════════════════════════
        # 模式A：ComfyUI（原有逻辑不动）
        # ══════════════════════════════════════════════════════
        if mode == "ComfyUI 两步处理":
            cfg1, cfg2 = st.columns([1, 2])
            with cfg1:
                orientation = st.radio(
                    "视频尺寸",
                    ["竖屏 720×1280", "横屏 1280×720"],
                    horizontal=True,
                    key="t6_orientation",
                )
                VW, VH = (720, 1280) if orientation == "竖屏 720×1280" else (1280, 720)

            with cfg2:
                comfy_url = st.text_input(
                    "ComfyUI地址",
                    value=st.session_state.get("comfy_url", "http://127.0.0.1:8188"),
                    key="t6_comfy_url",
                )
                if comfy_url != st.session_state.get("comfy_url", ""):
                    st.session_state["comfy_url"] = comfy_url

            done_n = sum(
                1 for _, _, p in all_cells
                if p and Path(str(p)).name.startswith("cm_") and os.path.exists(str(p))
            )
            st.caption(f"共 {total_n} 张  |  已转换 {done_n}  |  目标 {VW}×{VH}")
            st.progress(done_n / total_n if total_n else 0)
            st.divider()

            need_convert = [
                (sid, cn, p) for sid, cn, p in all_cells
                if not (Path(str(p)).name.startswith("cm_") and os.path.exists(str(p)))
            ]

            if st.button(
                f"⚡ 一键转换全部帧图 ({len(need_convert)}张)",
                type="primary",
                disabled=not need_convert,
                key="t6_convert_all_comfy",
            ):
                from comfyui_api import qwen_i2i, upscale_4x
                import shutil as _shutil

                progress_bar = st.progress(0)
                status_text  = st.empty()
                err_list     = []

                for idx, (sid, cn, img_path) in enumerate(need_convert):
                    try:
                        if not img_path or not os.path.exists(str(img_path)):
                            err_list.append(f"Scene{sid}-格{cn}：图片不存在")
                            continue

                        status_text.caption(f"{idx+1}/{len(need_convert)}：Scene {sid} 格{cn} — 4x放大")
                        upscaled_path = upscale_4x(
                            input_image_path=str(img_path),
                            server_url=comfy_url,
                        )

                        status_text.caption(f"{idx+1}/{len(need_convert)}：Scene {sid} 格{cn} — 尺寸转换 {VW}×{VH}")
                        new_path = qwen_i2i(
                            input_image_path=upscaled_path,
                            prompt="保持画面内容、风格、人物、场景完全不变，仅调整画面构图以适应新的宽高比例，适当裁剪或扩展边缘区域，保持主体完整无边框",
                            width=VW, height=VH,
                            steps=4, cfg=1.0,
                            server_url=comfy_url,
                        )
                        try:
                            os.remove(upscaled_path)
                        except Exception:
                            pass

                        if new_path and os.path.exists(new_path):
                            stem     = Path(img_path).stem
                            new_name = Path(img_path).parent / f"cm_{stem}.png"
                            _shutil.move(new_path, str(new_name))
                            new_path = str(new_name)

                            g = st.session_state["grid_state"][sid]
                            g["locked_cells"][cn] = new_path
                            st.session_state["grid_state"] = dict(st.session_state["grid_state"])

                            vs       = st.session_state.get("video_state", {})
                            beat_ids = g.get("beat_ids", [])
                            for key, vstate in vs.items():
                                if not isinstance(vstate, dict): continue
                                for cell_num, bid in enumerate(beat_ids, 1):
                                    if vstate.get("from_beat_id") == bid and cell_num == cn:
                                        vstate["from_img"] = new_path
                                    if vstate.get("to_beat_id") == bid and cell_num == cn:
                                        vstate["to_img"] = new_path
                            st.session_state["video_state"] = vs

                    except Exception as e:
                        err_list.append(f"Scene{sid}-格{cn}：{e}")

                    progress_bar.progress((idx + 1) / len(need_convert))

                status_text.empty()
                if err_list:
                    st.warning(f"⚠️ {len(err_list)} 张失败：" + "  |  ".join(err_list[:5]))
                else:
                    st.success(f"✅ 全部转换完成，共 {len(need_convert)} 张")
                st.rerun()


        # ── 逐Scene预览（两种模式共用）───────────────────────
        st.divider()
        prefix = "cm_"

        for sid, g in sorted(locked_segs.items()):
            locked_cells = {int(k): v for k, v in g.get("locked_cells", {}).items()}
            beat_ids     = g.get("beat_ids", [])
            cols_n       = g.get("cols", 3)

            beats        = st.session_state.get("beats", [])
            scene_name   = next(
                (b.get("scene_name", b.get("scene", "")) for b in beats if b.get("segment") == sid),
                ""
            )

            done_scene  = sum(
                1 for p in locked_cells.values()
                if p and Path(str(p)).name.startswith(prefix) and os.path.exists(str(p))
            )
            total_scene = len(locked_cells)

            with st.expander(
                f"Scene {sid} · {scene_name} · [{done_scene}/{total_scene} 已转换]",
                expanded=False,
            ):
                cell_nums = sorted(locked_cells.keys())
                for row_start in range(0, len(cell_nums), cols_n):
                    row_cells = cell_nums[row_start:row_start + cols_n]
                    c_cols    = st.columns(len(row_cells))
                    for ci, cn in enumerate(row_cells):
                        img_path = locked_cells[cn]
                        bid      = beat_ids[cn - 1] if cn - 1 < len(beat_ids) else ""
                        is_done  = Path(str(img_path)).name.startswith(prefix) and os.path.exists(str(img_path))
                        with c_cols[ci]:
                            st.caption(f"格{cn} {bid} {'✅' if is_done else '⬜'}")
                            if img_path and os.path.exists(str(img_path)):
                                st.image(img_path, width=180)
                            else:
                                st.caption("图片缺失")
with tab7:
    st.subheader("视频生成")
    st.caption("基于切分帧生成首尾帧视频，每两个相邻帧构成一对")

    beats        = st.session_state.get("beats", [])
    shot_fields  = st.session_state.get("shot_fields", {})
    grid_state   = st.session_state.get("grid_state", {})
    style_cfg    = st.session_state.get("style_config", {})
    suffix       = style_cfg.get("video_suffix", "cinematic")
    char_assets  = st.session_state.get("char_assets", {})
    scene_assets = st.session_state.get("scene_assets", {})
    story_tone   = st.session_state.get("story_tone", "")
    refined_script = st.session_state.get("refined_script", "")
 
    # ── 前置检查 ─────────────────────────────────────────────
    if not beats:
        st.warning("请先完成第一步剧本切分")
        st.stop()
 
    locked_segs = {
        sid: g for sid, g in grid_state.items()
        if g.get("status") == "locked" and g.get("locked_cells")
    }
    if not locked_segs:
        st.warning("请先在第五步完成宫格切分并锁定")
        st.stop()
 
    from video_prompter import run_all as vp_run_all
 
    # ── 初始化 video_state ───────────────────────────────────
    if "video_state" not in st.session_state:
        st.session_state["video_state"] = {}
    vs = st.session_state["video_state"]
 
    # video_state 结构：
    # vs[pair_key] = {
    #   "seg_id":       int
    #   "from_beat_id": str
    #   "to_beat_id":   str
    #   "from_img":     str  # 切分帧路径
    #   "to_img":       str  # 切分帧路径
    #   "video_prompt": str
    #   "dialogue":     str
    #   "motion":       str
    #   "duration_hint":str
    #   "draft_lens":   str
    #   "draft_motion": str
    #   "draft_cont":   str
    #   "video_path":   str | None  # 生成的视频路径
    #   "status":       "pending" / "prompt_ready" / "generating" / "done" / "failed"
    # }
 
    # ── 构建 pair_map：从 grid_state 的 locked_cells 提取帧图 ─
    segments: dict = {}
    for b in beats:
        segments.setdefault(b.get("segment", 0), []).append(b)
 
    # 确保panel_descs_map
    panel_descs_map = {}
    for sid, g in locked_segs.items():
        panel_descs_map[sid] = g.get("panel_descs", [])
 
    # 构建所有pair
    def _build_pairs():
        pairs = []
        for sid in sorted(locked_segs.keys()):
            g            = locked_segs[sid]
            locked_cells = {int(k): v for k, v in g.get("locked_cells", {}).items()}
            beat_ids     = g.get("beat_ids", [])
            cell_nums    = sorted(locked_cells.keys())
            for i in range(len(cell_nums) - 1):
                cn_a   = cell_nums[i]
                cn_b   = cell_nums[i + 1]
                bid_a  = beat_ids[cn_a - 1] if cn_a - 1 < len(beat_ids) else ""
                bid_b  = beat_ids[cn_b - 1] if cn_b - 1 < len(beat_ids) else ""
                img_a  = locked_cells[cn_a]
                img_b  = locked_cells[cn_b]
                key    = f"{bid_a}__{bid_b}"
                pairs.append({
                    "key":          key,
                    "seg_id":       sid,
                    "from_beat_id": bid_a,
                    "to_beat_id":   bid_b,
                    "from_img":     img_a,   # 始终从grid_state实时取
                    "to_img":       img_b,   # 始终从grid_state实时取
                    "pair_idx":     i,
                })
        return pairs
 
    all_pairs = _build_pairs()
 
    # 初始化未存在的pair
    for p in all_pairs:
        if p["key"] not in vs:
            vs[p["key"]] = {
                "seg_id":           p["seg_id"],
                "from_beat_id":     p["from_beat_id"],
                "to_beat_id":       p["to_beat_id"],
                "from_img":         p["from_img"],
                "to_img":           p["to_img"],
                "pair_idx":         p["pair_idx"],
                "video_prompt":     "",
                "dialogue":         "",
                "dialogue_emotion": "",
                "audio_type":       "",
                "motion":           "",
                "duration_hint":    "",
                "draft_lens":       "",
                "draft_motion":     "",
                "draft_cont":       "",
                "video_path":       None,
                "status":           "pending",
            }
        else:
            # key已存在（从存档加载）→ 用grid_state最新切分路径覆盖旧路径
            vs[p["key"]]["from_img"] = p["from_img"]
            vs[p["key"]]["to_img"]   = p["to_img"]
    st.session_state["video_state"] = vs
    st.session_state["video_state"] = vs
 
    # ── 进度统计 ─────────────────────────────────────────────
    total_pairs    = len(all_pairs)
    prompt_ready_n = sum(1 for p in all_pairs
                         if vs[p["key"]].get("status") != "pending")
    video_done_n   = sum(1 for p in all_pairs
                         if vs[p["key"]].get("status") == "done")
 
    c1, c2, c3 = st.columns(3)
    c1.metric("总片段数", total_pairs)
    c2.metric("已生成提示词", prompt_ready_n)
    c3.metric("已生成视频", video_done_n)
    st.progress(video_done_n / total_pairs if total_pairs else 0)
    st.divider()
 
    # ══════════════════════════════════════════════════════════
    # 批量操作
    # ══════════════════════════════════════════════════════════
    st.markdown("**批量操作**")
    bat1, bat2, bat3 = st.columns(3)
 
    # ── 批量生成视频提示词 ────────────────────────────────────
    with bat1:
        need_prompt = [p for p in all_pairs
                       if vs[p["key"]].get("status") == "pending"]
        if st.button(
            f"① 生成所有视频提示词 ({len(need_prompt)})",
            type="primary" if need_prompt else "secondary",
            disabled=not need_prompt,
            use_container_width=True,
            key="vt_batch_prompts",
        ):
            with st.spinner("三层架构生成视频提示词（Layer0全局→Layer1规划→Layer2精写）..."):
                try:
                    results = vp_run_all(
                        beats         = beats,
                        shot_fields   = shot_fields,
                        panel_descs_map = panel_descs_map,
                        char_assets   = char_assets,
                        scene_assets  = scene_assets,
                        style_suffix  = suffix,
                        story_tone    = story_tone,
                        refined_script = refined_script,
                    )
                    # 写回 video_state
                    for p in all_pairs:
                        key    = p["key"]
                        bid_a  = p["from_beat_id"]
                        r      = results.get(bid_a, {})
                        if r.get("video_prompt"):
                            vs[key].update({
                                "video_prompt":     r["video_prompt"],
                                "dialogue":         r.get("dialogue", ""),
                                "dialogue_emotion": r.get("dialogue_emotion", ""),
                                "audio_type":       r.get("audio_type", ""),
                                "motion":           r.get("motion", ""),
                                "duration_hint":    r.get("duration_hint", ""),
                                "draft_lens":       r.get("draft_ab", ""),
                                "draft_motion":     r.get("draft_ab", ""),
                                "draft_cont":       r.get("draft_c", ""),
                                "status":           "prompt_ready",
                            })
                    st.session_state["video_state"] = dict(vs)
                    st.rerun()
                except Exception as e:
                    st.error(f"生成失败：{e}")
 
    # ── 批量生成视频 ──────────────────────────────────────────
    with bat2:
        st.caption("💡 复制视频提示词到外部工具（Veo/Kling等）生成视频后，在下方各片段上传mp4")
 
    st.divider()
 
    # ══════════════════════════════════════════════════════════
    # 逐 segment 展示
    # ══════════════════════════════════════════════════════════
    for sid in sorted(locked_segs.keys()):
        seg_pairs = [p for p in all_pairs if p["seg_id"] == sid]
        if not seg_pairs:
            continue
 
        seg_desc  = segments.get(sid, [{}])[0].get("segment_desc", "")
        done_n    = sum(1 for p in seg_pairs
                        if vs[p["key"]].get("status") == "done")
 
        with st.expander(
            f"Segment {sid}  ·  {seg_desc}  ·  [{done_n}/{len(seg_pairs)} 已生成]",
            expanded=(done_n < len(seg_pairs)),
        ):
            for p in seg_pairs:
                key   = p["key"]
                state = vs[key]
                status = state.get("status", "pending")
 
                status_icon = {
                    "done":         "✅",
                    "prompt_ready": "📝",
                    "generating":   "⏳",
                    "failed":       "❌",
                    "pending":      "⬜",
                }.get(status, "⬜")
 
                st.markdown(
                    f"**{status_icon} 片段 {p['pair_idx']+1}**  "
                    f"`{p['from_beat_id']}` → `{p['to_beat_id']}`"
                )
 
                # ── 首尾帧预览 ──────────────────────────────
                img_c1, img_c2, vid_c = st.columns([1, 1, 2])
                with img_c1:
                    if state["from_img"] and _os.path.exists(str(state["from_img"])):
                        st.image(state["from_img"], caption="首帧", width=150)
                    else:
                        st.caption("首帧缺失")
                with img_c2:
                    if state["to_img"] and _os.path.exists(str(state["to_img"])):
                        st.image(state["to_img"], caption="尾帧", width=150)
                    else:
                        st.caption("尾帧缺失")
                with vid_c:
                    if status == "done" and state.get("video_path"):
                        vp = state["video_path"]
                        if _os.path.exists(str(vp)):
                            st.video(vp)
                        else:
                            st.caption("视频文件缺失，请重新上传")
                    # 上传视频
                    uploaded_v = st.file_uploader(
                        "⬆️ 上传视频mp4",
                        type=["mp4", "mov", "webm"],
                        key=f"vt_upload_{key}",
                        label_visibility="collapsed",
                    )
                    if uploaded_v is not None:
                        import os, time
                        os.makedirs("videos", exist_ok=True)
                        save_path = f"videos/{key[:40]}_{int(time.time())}.mp4"
                        with open(save_path, "wb") as _f:
                            _f.write(uploaded_v.getbuffer())
                        vs[key]["video_path"] = save_path
                        vs[key]["status"]     = "done"
                        st.session_state["video_state"] = dict(vs)
                        st.rerun()
 
                # ── 视频提示词展示/编辑 ──────────────────────
                if state.get("video_prompt"):
                    pt_key  = f"vt_show_pt_{key}"
                    pt_open = st.session_state.get(pt_key, False)
                    c_btn, c_info = st.columns([1, 3])
                    with c_btn:
                        if st.button(
                            "收起提示词 ▲" if pt_open else "📋 查看/编辑提示词 ▼",
                            key=f"vt_pt_btn_{key}",
                        ):
                            st.session_state[pt_key] = not pt_open
                            st.rerun()
                    with c_info:
                        info_parts = []
                        if state.get("audio_type"):
                            info_parts.append(f"音频：{state['audio_type']}")
                        if state.get("motion"):
                            info_parts.append(f"运镜：{state['motion']}")
                        if state.get("duration_hint"):
                            info_parts.append(f"时长：{state['duration_hint']}")
                        if state.get("dialogue"):
                            emotion = f"（{state['dialogue_emotion']}）" if state.get("dialogue_emotion") else ""
                            info_parts.append(f"台词{emotion}：{state['dialogue'][:30]}")
 
                    if st.session_state.get(pt_key, False):
                        new_prompt = st.text_area(
                            "视频提示词（可编辑）",
                            value=state["video_prompt"],
                            height=180,
                            key=f"vt_pt_area_{key}",
                        )
                        new_dialogue = st.text_input(
                            "台词",
                            value=state.get("dialogue", ""),
                            key=f"vt_dialogue_{key}",
                        )
                        new_motion = st.text_input(
                            "运镜指令",
                            value=state.get("motion", ""),
                            key=f"vt_motion_{key}",
                        )
 
                        # 专家草稿折叠查看
                        draft_key = f"vt_draft_{key}"
                        if st.button(
                            "收起专家草稿 ▲" if st.session_state.get(draft_key) else "查看专家草稿 ▼",
                            key=f"vt_draft_btn_{key}",
                        ):
                            st.session_state[draft_key] = not st.session_state.get(draft_key, False)
                            st.rerun()
                        if st.session_state.get(draft_key, False):
                            d1, d2, d3 = st.columns(3)
                            with d1:
                                st.caption("专家A（镜头）")
                                st.text(state.get("draft_lens", "")[:300])
                            with d2:
                                st.caption("专家B（动作）")
                                st.text(state.get("draft_motion", "")[:300])
                            with d3:
                                st.caption("专家C（连贯）")
                                st.text(state.get("draft_cont", "")[:300])
 
                        save_col, gen_col = st.columns([1, 1])
                        with save_col:
                            if st.button("💾 保存修改", key=f"vt_save_{key}", type="primary"):
                                vs[key].update({
                                    "video_prompt": new_prompt,
                                    "dialogue":     new_dialogue,
                                    "motion":       new_motion,
                                    "status":       "prompt_ready",
                                })
                                st.session_state[pt_key] = False
                                st.session_state["video_state"] = dict(vs)
                                st.rerun()
                        with gen_col:
                            st.caption("生成视频后在上方上传mp4")
 
                # ── 单独重新生成提示词 ──────────────────────
                if status not in ("pending",):
                    regen_col, _ = st.columns([1, 3])
                    with regen_col:
                        if st.button("🔄 重新生成提示词", key=f"vt_regen_pt_{key}"):
                            with st.spinner("重新生成..."):
                                try:
                                    from video_prompter import run_segment as vp_run_seg
                                    seg_results = vp_run_seg(
                                        seg_id          = p["seg_id"],
                                        beats           = beats,
                                        shot_fields     = shot_fields,
                                        panel_descs_map = panel_descs_map,
                                        char_assets     = char_assets,
                                        scene_assets    = scene_assets,
                                        style_suffix    = suffix,
                                        story_tone      = story_tone,
                                        refined_script  = refined_script,
                                        existing_results = {
                                            v["from_beat_id"]: v
                                            for v in vs.values()
                                            if isinstance(v, dict)
                                        },
                                    )
                                    bid_a = p["from_beat_id"]
                                    r     = seg_results.get(bid_a, {})
                                    if r.get("video_prompt"):
                                        vs[key].update({
                                            "video_prompt":  r["video_prompt"],
                                            "dialogue":      r.get("dialogue", ""),
                                            "motion":        r.get("motion", ""),
                                            "duration_hint": r.get("duration_hint", ""),
                                            "draft_lens":    r.get("draft_lens", ""),
                                            "draft_motion":  r.get("draft_motion", ""),
                                            "draft_cont":    r.get("draft_cont", ""),
                                            "status":        "prompt_ready",
                                        })
                                        st.session_state["video_state"] = dict(vs)
                                        st.rerun()
                                except Exception as e:
                                    st.error(f"失败：{e}")
 
                st.divider()

with tab8:
    st.subheader("📊 超级表格")
    st.caption("实时展示所有beat的完整字段，随各Tab进度自动更新")

    beats       = st.session_state.get("beats", [])
    shot_fields = st.session_state.get("shot_fields", {})
    grid_state  = st.session_state.get("grid_state", {})
    video_state = st.session_state.get("video_state", {})

    if not beats:
        st.warning("请先完成第一步剧本切分")
    else:
        # ── 构建辅助反查表 ────────────────────────────────
        # frame_img反查：beat_id → 帧图路径
        cell_img = {}
        for sid, gs in grid_state.items():
            locked_cells = {int(k): v for k, v in gs.get("locked_cells", {}).items()}
            for i, bid in enumerate(gs.get("beat_ids", [])):
                cn = i + 1
                if cn in locked_cells and locked_cells[cn]:
                    cell_img[bid] = locked_cells[cn]

        # panel_desc反查：beat_id → panel_desc文本
        panel_desc_map = {}
        for sid, gs in grid_state.items():
            for pd in gs.get("panel_descs", []):
                bid = pd.get("beat_id", "")
                if bid:
                    panel_desc_map[bid] = pd.get("desc", "")

        # video反查：beat_id → video_state entry
        video_by_beat = {}
        for vs in video_state.values():
            if not isinstance(vs, dict):
                continue
            for key in ["from_beat_id", "to_beat_id"]:
                bid = vs.get(key, "")
                if bid:
                    video_by_beat[bid] = vs

        # ── 拼装超级表格 ──────────────────────────────────
        rows = []
        for b in beats:
            bid = b.get("beat_id", "")
            sf  = shot_fields.get(bid, {})
            vs  = video_by_beat.get(bid, {})

            rows.append({
                # ── 归属
                "scene_id":   b.get("segment", ""),
                "scene":      b.get("scene_name", b.get("scene", "")),
                "segment":    b.get("parent_segment_name", ""),
                "hook":       b.get("hook_type", ""),

                # ── 镜头基础
                "beat_id":    bid,
                "flag":       "🔵" if b.get("type_flag") == "supplement" else "⚪",
                "type":       b.get("type", ""),
                "line":       b.get("line", ""),
                "audio":      b.get("audio_type") or sf.get("audio_type", ""),
                "speaker":    b.get("speaker", ""),
                "chars":      " / ".join(b.get("characters") or []),
                "raw_text":   b.get("raw_text", ""),

                # ── 台词（Tab4）
                "dialogue":   sf.get("dialogue", ""),
                "emotion":    sf.get("dialogue_emotion", ""),

                # ── 拍摄参数（Tab4）
                "scale":      sf.get("shot_scale", ""),
                "angle":      sf.get("camera_angle", ""),
                "lighting":   sf.get("lighting", ""),
                "mood":       sf.get("mood", ""),
                "energy":     sf.get("energy_type", ""),
                "vec":        sf.get("motion_vector", ""),
                "dur":        sf.get("duration_hint", ""),
                "subject":    sf.get("subject", "")[:50] if sf.get("subject") else "",

                # ── 格子描述（Tab5）
                "panel_desc": panel_desc_map.get(bid, "")[:50]
                              if panel_desc_map.get(bid) else "",

                # ── 状态列
                "Tab4": "✅" if sf.get("status") in ("done", "edited") else "⬜",
                "Tab5": "✅" if bid in cell_img else "⬜",
                "Tab7": "✅" if vs.get("status") == "done" else "⬜",
            })

        import pandas as _pd
        df = _pd.DataFrame(rows)

        # ── 进度概览 ──────────────────────────────────────
        total = len(rows)
        orig  = sum(1 for r in rows if r["flag"] == "⚪")
        supp  = sum(1 for r in rows if r["flag"] == "🔵")
        t4_n  = sum(1 for r in rows if r["Tab4"] == "✅")
        t5_n  = sum(1 for r in rows if r["Tab5"] == "✅")
        t7_n  = sum(1 for r in rows if r["Tab7"] == "✅")

        mc1, mc2, mc3, mc4, mc5, mc6 = st.columns(6)
        mc1.metric("总beat",    total)
        mc2.metric("原始",      orig)
        mc3.metric("补全",      supp)
        mc4.metric("Tab4完成",  f"{t4_n}/{total}")
        mc5.metric("Tab5完成",  f"{t5_n}/{total}")
        mc6.metric("Tab7完成",  f"{t7_n}/{total}")

        st.progress(t4_n / total if total else 0,
                    text=f"Tab4扩写进度 {t4_n}/{total}")
        st.progress(t5_n / total if total else 0,
                    text=f"Tab5生图进度 {t5_n}/{total}")
        st.progress(t7_n / total if total else 0,
                    text=f"Tab7视频进度 {t7_n}/{total}")

        st.divider()

        # ── 筛选栏 ────────────────────────────────────────
        fc1, fc2, fc3, fc4 = st.columns(4)
        with fc1:
            scene_opts = ["全部"] + sorted(
                df["scene"].dropna().unique().tolist())
            sel_scene = st.selectbox("场景", scene_opts, key="t8_scene")
        with fc2:
            type_opts = ["全部"] + sorted(
                df["type"].dropna().unique().tolist())
            sel_type = st.selectbox("类型", type_opts, key="t8_type")
        with fc3:
            audio_opts = ["全部"] + sorted(
                df["audio"].dropna().unique().tolist())
            sel_audio = st.selectbox("音频类型", audio_opts, key="t8_audio")
        with fc4:
            status_opts = ["全部", "Tab4未完成", "Tab5未完成",
                           "Tab7未完成", "仅原始beat", "仅补全beat"]
            sel_status = st.selectbox("状态", status_opts, key="t8_status")

        dff = df.copy()
        if sel_scene != "全部":
            dff = dff[dff["scene"] == sel_scene]
        if sel_type != "全部":
            dff = dff[dff["type"] == sel_type]
        if sel_audio != "全部":
            dff = dff[dff["audio"] == sel_audio]
        if sel_status == "Tab4未完成":
            dff = dff[dff["Tab4"] == "⬜"]
        elif sel_status == "Tab5未完成":
            dff = dff[dff["Tab5"] == "⬜"]
        elif sel_status == "Tab7未完成":
            dff = dff[dff["Tab7"] == "⬜"]
        elif sel_status == "仅原始beat":
            dff = dff[dff["flag"] == "⚪"]
        elif sel_status == "仅补全beat":
            dff = dff[dff["flag"] == "🔵"]

        st.caption(f"显示 {len(dff)} / {total} 行")

        # ── 列显示控制 ────────────────────────────────────
        with st.expander("⚙️ 列显示控制", expanded=False):
            col_groups = {
                "归属列":   ["scene_id", "scene", "segment", "hook"],
                "镜头基础": ["flag", "type", "audio", "speaker",
                             "chars", "raw_text"],
                "台词":     ["dialogue", "emotion"],
                "拍摄参数": ["scale", "angle", "lighting", "mood",
                             "energy", "vec", "dur", "subject"],
                "格子描述": ["panel_desc"],
                "状态":     ["Tab4", "Tab5", "Tab7"],
            }
            show_groups = {}
            gc1, gc2, gc3 = st.columns(3)
            for i, (grp, cols) in enumerate(col_groups.items()):
                with [gc1, gc2, gc3][i % 3]:
                    show_groups[grp] = st.checkbox(
                        grp, value=True, key=f"t8_grp_{grp}")

        # 根据勾选决定显示哪些列
        visible_cols = ["beat_id"]  # beat_id始终显示
        for grp, cols in col_groups.items():
            if show_groups.get(grp, True):
                visible_cols += [c for c in cols if c in dff.columns]
        visible_cols = list(dict.fromkeys(visible_cols))  # 去重保序
        dff_show = dff[visible_cols]

        # ── 表格展示 ──────────────────────────────────────
        st.dataframe(
            dff_show,
            hide_index=True,
            use_container_width=True,
            height=600,
            column_config={
                "beat_id":    st.column_config.TextColumn("beat_id",   width=90),
                "flag":       st.column_config.TextColumn("",          width=28),
                "scene_id":   st.column_config.TextColumn("scene_id",  width=60),
                "scene":      st.column_config.TextColumn("场景",      width=110),
                "segment":    st.column_config.TextColumn("叙事段",    width=110),
                "hook":       st.column_config.TextColumn("hook",      width=80),
                "type":       st.column_config.TextColumn("type",      width=55),
                "line":       st.column_config.TextColumn("台词",      width=150),
                "audio":      st.column_config.TextColumn("音频",      width=70),
                "speaker":    st.column_config.TextColumn("说话人",    width=65),
                "chars":      st.column_config.TextColumn("人物",      width=90),
                "raw_text":   st.column_config.TextColumn("原文",      width=180),
                "dialogue":   st.column_config.TextColumn("台词",      width=110),
                "emotion":    st.column_config.TextColumn("台词情绪",  width=75),
                "scale":      st.column_config.TextColumn("景别",      width=50),
                "angle":      st.column_config.TextColumn("机位",      width=80),
                "lighting":   st.column_config.TextColumn("光线",      width=85),
                "mood":       st.column_config.TextColumn("情绪",      width=70),
                "energy":     st.column_config.TextColumn("能量",      width=80),
                "vec":        st.column_config.TextColumn("动势线",    width=100),
                "dur":        st.column_config.TextColumn("时长(s)",   width=55),
                "subject":    st.column_config.TextColumn("画面骨架",  width=200),
                "panel_desc": st.column_config.TextColumn("格子描述",  width=200),
                "Tab4":       st.column_config.TextColumn("Tab4",      width=42),
                "Tab5":       st.column_config.TextColumn("Tab5",      width=42),
                "Tab7":       st.column_config.TextColumn("Tab7",      width=42),
            },
        )

        # ── 选中行详情 ────────────────────────────────────
        st.divider()
        st.markdown("**查看beat完整字段**")
        sel_bid = st.selectbox(
            "选择beat_id",
            options=dff["beat_id"].tolist(),
            key="t8_sel_beat",
            label_visibility="collapsed",
        )
        if sel_bid:
            b   = next((x for x in beats if x.get("beat_id") == sel_bid), {})
            sf  = shot_fields.get(sel_bid, {})
            vs  = video_by_beat.get(sel_bid, {})

            d1, d2, d3 = st.columns(3)
            with d1:
                st.markdown("**Tab0/1 基础字段**")
                st.json({
                    "beat_id":                b.get("beat_id"),
                    "type":                   b.get("type"),
                    "line":                   b.get("line", ""),
                    "audio_type":             b.get("audio_type"),
                    "type_flag":              b.get("type_flag"),
                    "speaker":                b.get("speaker"),
                    "characters":             b.get("characters"),
                    "raw_text":               b.get("raw_text"),
                    "dialogues":              b.get("dialogues", []),
                    "scene_name":             b.get("scene_name"),
                    "scene":                  b.get("scene"),
                    "segment":                b.get("segment"),
                    "parent_segment_id":      b.get("parent_segment_id"),
                    "parent_segment_name":    b.get("parent_segment_name"),
                    "space_desc":             b.get("space_desc"),
                    "hook_type":              b.get("hook_type"),
                    "dialogue_structure_ref": b.get("dialogue_structure_ref", -1),
                    "dialogue_structure":     b.get("dialogue_structure"),
                })
            with d2:
                st.markdown("**Tab4 字段扩写**")
                st.json({
                    "status":           sf.get("status"),
                    "shot_scale":       sf.get("shot_scale"),
                    "camera_angle":     sf.get("camera_angle"),
                    "lighting":         sf.get("lighting"),
                    "mood":             sf.get("mood"),
                    "energy_type":      sf.get("energy_type"),
                    "motion_vector":    sf.get("motion_vector"),
                    "audio_type":       sf.get("audio_type"),
                    "dialogue":         sf.get("dialogue"),
                    "dialogue_emotion": sf.get("dialogue_emotion"),
                    "duration_hint":    sf.get("duration_hint"),
                    "subject":          sf.get("subject"),
                    "rationale":        sf.get("rationale"),
                    "reasoning":        sf.get("reasoning"),
                    "step3_checks":     sf.get("step3_checks"),
                })
            with d3:
                st.markdown("**Tab5/7 生图与视频**")
                st.json({
                    "panel_desc":   panel_desc_map.get(sel_bid, ""),
                    "frame_img":    cell_img.get(sel_bid, ""),
                    "video_prompt": vs.get("video_prompt", "")[:200]
                                    if vs.get("video_prompt") else "",
                    "dialogue":     vs.get("dialogue", ""),
                    "audio_type":   vs.get("audio_type", ""),
                    "motion":       vs.get("motion", ""),
                    "duration":     vs.get("duration_hint", ""),
                    "video_path":   vs.get("video_path", ""),
                    "status":       vs.get("status", ""),
                })

            # 帧图预览
            if cell_img.get(sel_bid):
                import os as _os
                img_path = cell_img[sel_bid]
                if _os.path.exists(str(img_path)):
                    st.image(img_path, caption=f"帧图：{sel_bid}", width=200)

        # ── 导出CSV ───────────────────────────────────────
        st.divider()
        st.download_button(
            "⬇️ 导出完整表格CSV",
            data=df.to_csv(index=False, encoding="utf-8-sig"),
            file_name="super_table.csv",
            mime="text/csv",
            key="t8_download",
        )
