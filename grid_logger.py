"""
grid_logger.py — Tab5 生图日志

每次生图操作都追加写入 logs/grid_log.json
格式：每条记录一个JSON对象，换行分隔（JSONL格式，方便追加）

每条记录包含：
  timestamp       生图时间
  seg_id          段落编号
  seg_desc        段落描述
  generation      第几代
  action          操作类型：gen_first / evolve / regen_all
  rows / cols     宫格规格
  grid_prompt     完整宫格提示词（三张相同）
  panel_descs     逐Panel描述列表
  ref_notes       参考图说明
  selected_cells  本次进化勾选的格子（evolve时有效）
  user_note       用户调整说明
  result_urls     生图结果路径列表（None表示该张失败）
  success_count   成功生成几张
"""

import json
import os
from datetime import datetime

LOG_DIR  = "logs"
LOG_FILE = os.path.join(LOG_DIR, "grid_log.json")


def _ensure_dir():
    os.makedirs(LOG_DIR, exist_ok=True)


def write_log(
    seg_id: int,
    seg_desc: str,
    generation: int,
    action: str,
    rows: int,
    cols: int,
    grid_prompt: str,
    panel_descs: list,
    ref_notes: str,
    result_urls: list,
    selected_cells: list = None,
    user_note: str = "",
):
    """
    追加写入一条生图日志

    action 取值：
      gen_first   第0代生图
      evolve      格子级进化
      regen_all   整体重新生成
    """
    _ensure_dir()

    record = {
        "timestamp":      datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "seg_id":         seg_id,
        "seg_desc":       seg_desc,
        "generation":     generation,
        "action":         action,
        "rows":           rows,
        "cols":           cols,
        "grid_prompt":    grid_prompt,
        "panel_descs":    panel_descs,
        "ref_notes":      ref_notes,
        "selected_cells": selected_cells or [],
        "user_note":      user_note or "",
        "result_urls":    result_urls,
        "success_count":  sum(1 for u in result_urls if u),
    }

    # JSONL格式：每条记录一行
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_logs(seg_id: int = None) -> list[dict]:
    """
    读取日志，可按seg_id过滤
    返回：记录列表，最新的在前
    """
    _ensure_dir()
    if not os.path.exists(LOG_FILE):
        return []

    records = []
    with open(LOG_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                if seg_id is None or r.get("seg_id") == seg_id:
                    records.append(r)
            except Exception:
                continue

    return list(reversed(records))  # 最新的在前


def get_log_path() -> str:
    return os.path.abspath(LOG_FILE)