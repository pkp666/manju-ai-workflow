"""
debug_logger.py — Tab运行调试日志

每次Tab完成后调用save_tab_snapshot()
在json/tab{N}_{随机ID}/snapshot.json保存当前关键数据

用法：
    from debug_logger import save_tab_snapshot
    save_tab_snapshot(tab_num=0, session_state=st.session_state)
"""

import json
import os
import uuid
import time
from datetime import datetime


# ── 每个Tab需要保存的字段 ────────────────────────────────────

TAB_FIELDS = {
    0: [
        "story_tone",
        "chars_init",
        "scenes_init",
        "props_init",
        "raw_dialogues",
        "rs",           # 包含segments/drama_segments/scenes等
    ],
    1: [
        "story_tone",
        "chars_init",
        "beats",
        "grid_map",
    ],
    2: [
        "style_config",
    ],
    3: [
        "chars",
        "char_assets",
        "scene_assets",
        "prop_assets",
    ],
    4: [
        "beats",
        "shot_fields",
    ],
    5: [
        "grid_state",
    ],
    6: [
        "grid_state",   # 单帧遗传结果
    ],
    7: [
        "video_state",
    ],
}


def _make_serializable(obj, depth=0):
    """
    把session_state里的对象转成JSON可序列化的格式。
    depth限制递归深度，防止超大对象。
    """
    if depth > 10:
        return "...(depth limit)"

    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, (list, tuple)):
        return [_make_serializable(i, depth+1) for i in obj]
    if isinstance(obj, dict):
        return {
            str(k): _make_serializable(v, depth+1)
            for k, v in obj.items()
        }
    # 其他类型转字符串
    return str(obj)


def _truncate_long_strings(obj, max_len=500, depth=0):
    """
    截断过长的字符串，避免json文件太大。
    但保留列表和字典的结构。
    """
    if depth > 10:
        return obj
    if isinstance(obj, str):
        if len(obj) > max_len:
            return obj[:max_len] + f"...(截断，原长{len(obj)}字)"
        return obj
    if isinstance(obj, list):
        return [_truncate_long_strings(i, max_len, depth+1) for i in obj]
    if isinstance(obj, dict):
        return {
            k: _truncate_long_strings(v, max_len, depth+1)
            for k, v in obj.items()
        }
    return obj


def save_tab_snapshot(
    tab_num: int,
    session_state: dict,
    label: str = "",
    truncate: bool = True,
    max_str_len: int = 500,
) -> str:
    """
    保存Tab运行后的session_state快照。

    参数：
        tab_num       Tab编号（0-7）
        session_state st.session_state
        label         额外标注，如"切分完成"/"改编完成"
        truncate      是否截断长字符串（默认True）
        max_str_len   截断长度（默认500字）

    返回：
        保存的json文件路径
    """
    # 生成文件夹名：tab{N}_{随机ID}
    run_id   = uuid.uuid4().hex[:8]
    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    folder   = f"tab{tab_num}_{ts}_{run_id}"
    if label:
        # 把label里的特殊字符替换掉
        safe_label = label.replace("/", "_").replace("\\", "_").replace(" ", "_")
        folder = f"tab{tab_num}_{safe_label}_{ts}_{run_id}"

    save_dir = os.path.join("json", folder)
    os.makedirs(save_dir, exist_ok=True)

    # 取本Tab需要的字段
    fields   = TAB_FIELDS.get(tab_num, [])
    snapshot = {
        "__meta__": {
            "tab":       tab_num,
            "label":     label,
            "timestamp": datetime.now().isoformat(),
            "folder":    folder,
            "fields":    fields,
        }
    }

    for field in fields:
        val = session_state.get(field)
        if val is None:
            snapshot[field] = None
            continue
        try:
            serialized = _make_serializable(val)
            if truncate:
                serialized = _truncate_long_strings(serialized, max_str_len)
            snapshot[field] = serialized
        except Exception as e:
            snapshot[field] = f"[序列化失败: {e}]"

    # 写入json
    save_path = os.path.join(save_dir, "snapshot.json")
    with open(save_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)

    print(f"[debug_logger] Tab{tab_num} 快照已保存：{save_path}")
    return save_path


def save_custom_snapshot(
    name: str,
    data: dict,
    truncate: bool = True,
    max_str_len: int = 500,
) -> str:
    """
    保存自定义快照（不限于Tab边界）。

    参数：
        name   文件夹名前缀
        data   要保存的数据字典
    """
    run_id   = uuid.uuid4().hex[:8]
    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    folder   = f"{name}_{ts}_{run_id}"
    save_dir = os.path.join("json", folder)
    os.makedirs(save_dir, exist_ok=True)

    snapshot = {
        "__meta__": {
            "name":      name,
            "timestamp": datetime.now().isoformat(),
            "folder":    folder,
        },
        **{
            k: (
                _truncate_long_strings(
                    _make_serializable(v), max_str_len
                ) if truncate else _make_serializable(v)
            )
            for k, v in data.items()
        }
    }

    save_path = os.path.join(save_dir, "snapshot.json")
    with open(save_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)

    print(f"[debug_logger] 自定义快照已保存：{save_path}")
    return save_path


def list_snapshots(tab_num: int | None = None) -> list[dict]:
    """
    列出所有快照（或某个Tab的快照）。
    返回：[{folder, path, tab, timestamp, label}]
    """
    base_dir = "json"
    if not os.path.exists(base_dir):
        return []

    result = []
    for folder in sorted(os.listdir(base_dir), reverse=True):
        folder_path = os.path.join(base_dir, folder)
        snap_path   = os.path.join(folder_path, "snapshot.json")
        if not os.path.exists(snap_path):
            continue

        # 从文件夹名解析tab编号
        try:
            tab = int(folder.split("_")[0].replace("tab", ""))
        except Exception:
            tab = -1

        if tab_num is not None and tab != tab_num:
            continue

        result.append({
            "folder":    folder,
            "path":      snap_path,
            "tab":       tab,
        })

    return result