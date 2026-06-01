import streamlit as st
import requests
import time
import threading
import queue
import os
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("GRSAI_API_KEY", "")

BASE_URL = "https://grsaiapi.com"


def get_headers():
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_KEY}"
    }


def submit_task(prompt, model, size):
    resp = requests.post(
        f"{BASE_URL}/v1/draw/completions",
        headers=get_headers(),
        json={
            "model": model,
            "prompt": prompt,
            "size": size,
            "shutProgress": True,
            "webHook": "-1"
        },
        timeout=30
    )
    raw = resp.json()
    print(f"[submit] HTTP {resp.status_code} | body: {raw}")
    if not isinstance(raw, dict):
        raise ValueError(f"非 dict 响应: {raw}")
    if raw.get("code") != 0:
        raise ValueError(f"code={raw.get('code')} msg={raw.get('msg')} data={raw.get('data')}")
    data = raw.get("data") or {}
    task_id = data.get("id")
    if not task_id:
        raise ValueError(f"data 中无 id，完整响应: {raw}")
    return task_id


def poll_task(task_id, timeout=180, interval=3):
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(interval)
        r = requests.post(
            f"{BASE_URL}/v1/draw/result",
            headers=get_headers(),
            json={"id": task_id},
            timeout=15
        )
        raw = r.json()
        print(f"[poll {task_id}] HTTP {r.status_code} | body: {raw}")
        if not isinstance(raw, dict):
            print(f"[poll] 非 dict 响应，跳过本次: {raw}")
            continue
        res = raw.get("data") or {}
        status = res.get("status")
        if status == "succeeded":
            return {"status": "succeeded", "results": res.get("results", [])}
        elif status == "failed":
            return {"status": "failed", "reason": res.get("failure_reason"), "error": res.get("error")}
        # status == "running" 或其他，继续等待
    return {"status": "timeout"}


def worker(index, prompt, model, size, result_queue):
    """子线程：只做 HTTP，结果放入队列，不碰任何 st.* 对象"""
    try:
        task_id = submit_task(prompt, model, size)
        if not task_id:
            result_queue.put({"index": index, "status": "failed",
                              "reason": "提交失败", "error": "未获取到 task_id"})
            return
        result = poll_task(task_id)
        result["index"] = index
        result_queue.put(result)
    except Exception as e:
        result_queue.put({"index": index, "status": "failed",
                          "reason": "异常", "error": str(e)})


# ========== UI ==========
st.title("🎨 AI 图像批量生成")

model = st.selectbox("模型", ["sora-image", "gpt-image-1.5"])
size = st.selectbox("比例", ["1:1", "3:2", "2:3", "auto"])

st.markdown("**提示词列表**（每行一个）")
prompts_input = st.text_area(
    "提示词",
    placeholder="一只猫咪在草地上玩耍\n星空下的城市夜景\n赛博朋克风格的街道",
    height=150,
    label_visibility="collapsed"
)

prompts = [p.strip() for p in prompts_input.strip().splitlines() if p.strip()]
if prompts:
    st.caption(f"共 {len(prompts)} 个任务")

if st.button("🚀 并行生成", type="primary", disabled=not prompts):
    st.divider()
    total = len(prompts)
    cols_per_row = 3

    # 主线程预先创建好所有占位符
    placeholders = []
    for row_start in range(0, total, cols_per_row):
        row_slice = prompts[row_start: row_start + cols_per_row]
        cols = st.columns(len(row_slice))
        for col in cols:
            with col:
                placeholders.append({
                    "status": st.empty(),
                    "image": st.empty(),
                    "caption": st.empty()
                })

    for i, ph in enumerate(placeholders):
        ph["status"].info(f"⏳ 任务 {i+1} 排队中…")
        ph["caption"].caption(prompts[i])

    # 启动子线程，结果统一写入队列
    result_queue = queue.Queue()
    threads = [
        threading.Thread(target=worker, args=(i, prompts[i], model, size, result_queue), daemon=True)
        for i in range(total)
    ]
    for t in threads:
        t.start()

    # 主线程轮询队列，收到结果立刻更新对应占位符
    completed = 0
    while completed < total:
        try:
            result = result_queue.get(timeout=1)  # 短超时，保持主线程响应
        except queue.Empty:
            continue

        idx = result["index"]
        ph = placeholders[idx]
        ph["status"].empty()

        if result["status"] == "succeeded":
            imgs = result["results"]
            ph["image"].image(imgs[0]["url"], width=400)
            ph["caption"].caption(f"✅ {prompts[idx]}")
        elif result["status"] == "timeout":
            ph["status"].warning("⏰ 超时")
        else:
            ph["status"].error(f"❌ {result.get('reason', '')}：{result.get('error', '')}")

        completed += 1

    for t in threads:
        t.join()
