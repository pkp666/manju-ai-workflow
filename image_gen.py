"""
image_gen.py  —  生图模块
对外接口：
  generate_images(prompts, backend, model, size) -> list[str|None]
  generate_shot_images(prompts, ref_chars, ref_scene, upload_server, backend, model, size) -> list[str|None]

backend 取值：
  "sora"   → /v1/draw/completions
  "banana" → /v1/draw/nano-banana，优先4K，失败自动降级2K

返回本地相对路径，图片保存在 images/ 目录下
"""
import os
import time
import threading
import queue
import requests
from pathlib import Path
import uuid
from dotenv import load_dotenv

load_dotenv()

API_KEY  = os.getenv("GRSAI_API_KEY", "")
BASE_URL = os.getenv("GRSAI_BASE_URL", "https://grsaiapi.com")
HEADERS  = {"Content-Type": "application/json", "Authorization": f"Bearer {API_KEY}"}
IMG_DIR  = os.getenv("MANJU_IMAGE_DIR", "images")

os.makedirs(IMG_DIR, exist_ok=True)

# ── 模型列表（供 UI 展示）────────────────────────────────────
SORA_MODELS = ["sora-image", "sora-image-pro"]
BANANA_MODELS = [
    "nano-banana-fast", "nano-banana", "nano-banana-pro",
    "nano-banana-pro-vt", "nano-banana-2", "nano-banana-2-cl",
]


# ── 图片上传中转 ──────────────────────────────────────────────

def _upload_image(image_path: str, server_url: str) -> str | None:
    if not image_path or not os.path.exists(image_path):
        return None
    try:
        upload_url = f"{server_url.rstrip('/')}/upload/image"
        with open(image_path, "rb") as f:
            files = {"image": (Path(image_path).name, f, "image/png")}
            r = requests.post(
                upload_url,
                files=files,
                headers={"ngrok-skip-browser-warning": "true"},
                timeout=60
            )
        if r.status_code == 200:
            result = r.json()
            return result.get("url") or (result.get("data") or {}).get("url")
        return None
    except Exception as e:
        print(f"[image_gen] 上传失败 {image_path}: {e}")
        return None


def _upload_refs(ref_paths: list[str], server_url: str) -> list[str]:
    if not server_url:
        return []
    urls = [None] * len(ref_paths)
    lock = threading.Lock()

    def worker(i, path):
        url = _upload_image(path, server_url)
        with lock:
            urls[i] = url

    threads = [
        threading.Thread(target=worker, args=(i, p), daemon=True)
        for i, p in enumerate(ref_paths) if p and os.path.exists(p)
    ]
    for t in threads: t.start()
    for t in threads: t.join()
    return [u for u in urls if u]


# ── 轮询 & 下载（公用）──────────────────────────────────────

def _poll(task_id: str, timeout: int = 300) -> str | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(3)
        r = requests.post(
            f"{BASE_URL}/v1/draw/result",
            headers=HEADERS,
            json={"id": task_id},
            timeout=15
        )
        res    = (r.json().get("data") or {})
        status = res.get("status")
        if status == "succeeded":
            results = res.get("results", [])
            return results[0]["url"] if results else None
        elif status == "failed":
            print(f"[image_gen] 生图失败: {res.get('failure_reason')} {res.get('error')}")
            return None
    return None


def _download(url: str, index: int) -> str | None:
    try:
        r = requests.get(url, timeout=60)
        r.raise_for_status()
        filename = f"{IMG_DIR}/{int(time.time())}_{index}_{uuid.uuid4().hex[:8]}.png"
        with open(filename, "wb") as f:
            f.write(r.content)
        return filename
    except Exception:
        return None


# ── 按 backend 独立提交 ──────────────────────────────────────

def _submit_sora(prompt: str, model: str, size: str,
                 ref_urls: list[str] | None = None) -> str:
    payload = {
        "model": model,
        "prompt": prompt,
        "size": size,
        "shutProgress": True,
        "webHook": "-1",
    }
    if ref_urls:
        payload["urls"] = ref_urls
    r   = requests.post(f"{BASE_URL}/v1/draw/completions", headers=HEADERS, json=payload, timeout=30)
    raw = r.json()
    if raw.get("code") != 0:
        raise ValueError(f"提交失败: {raw.get('msg')}")
    task_id = (raw.get("data") or {}).get("id")
    if not task_id:
        raise ValueError(f"未获取到 task_id: {raw}")
    return task_id


def _submit_banana(prompt: str, model: str, size: str,
                   ref_urls: list[str] | None = None,
                   image_size: str = "4K") -> str:
    payload = {
        "model": model,
        "prompt": prompt,
        "aspectRatio": size,
        "imageSize": image_size,
        "shutProgress": True,
        "webHook": "-1",
    }
    if ref_urls:
        payload["urls"] = ref_urls
    r   = requests.post(f"{BASE_URL}/v1/draw/nano-banana", headers=HEADERS, json=payload, timeout=30)
    raw = r.json()
    if raw.get("code") != 0:
        raise ValueError(f"提交失败: {raw.get('msg')}")
    task_id = (raw.get("data") or {}).get("id")
    if not task_id:
        raise ValueError(f"未获取到 task_id: {raw}")
    return task_id


# ── worker：按 backend 调度，banana 自动降级 ─────────────────

def _worker(index: int, prompt: str, model: str, size: str,
            q: queue.Queue, ref_urls: list[str] | None = None,
            backend: str = "sora"):
    try:
        url = None

        if backend == "banana":
            for image_size in ["4K", "2K"]:
                try:
                    task_id = _submit_banana(prompt, model, size, ref_urls, image_size)
                    url     = _poll(task_id)
                    if url:
                        print(f"[image_gen] banana {image_size} 成功 index={index}")
                        break
                    print(f"[image_gen] banana {image_size} 返回空，尝试降级")
                except Exception as e:
                    print(f"[image_gen] banana {image_size} 失败 index={index}: {e}")
        else:
            task_id = _submit_sora(prompt, model, size, ref_urls)
            url     = _poll(task_id)

        if url:
            local_path = _download(url, index)
            q.put({"index": index, "url": local_path or url})
        else:
            q.put({"index": index, "url": None})

    except Exception as e:
        print(f"[image_gen] worker error index={index}: {e}")
        q.put({"index": index, "url": None, "error": str(e)})


# ── 对外接口 ─────────────────────────────────────────────────

def generate_images(
    prompts: list[str],
    backend: str = "sora",
    model: str = "sora-image",
    size: str = "1:1",
) -> list[str | None]:
    total   = len(prompts)
    results = [None] * total
    q       = queue.Queue()

    threads = [
        threading.Thread(
            target=_worker,
            args=(i, p, model, size, q, None, backend),
            daemon=True
        )
        for i, p in enumerate(prompts)
    ]
    for t in threads: t.start()
    for _ in range(total):
        item = q.get()
        results[item["index"]] = item.get("url")
    for t in threads: t.join()
    return results


def generate_shot_images(
    prompts: list[str],
    ref_chars: list[str],
    ref_scene: str | None,
    upload_server: str = "",
    backend: str = "sora",
    model: str = "sora-image",
    size: str = "1:1",
) -> list[str | None]:
    upload_server = upload_server or os.getenv("MANJU_IMAGE_RELAY_BASE_URL", "")
    ref_paths = [p for p in ref_chars if p and os.path.exists(p)]
    if ref_scene and os.path.exists(ref_scene):
        ref_paths.append(ref_scene)

    if not ref_paths:
        print("[image_gen] 无参考图，使用普通生图")
        return generate_images(prompts, backend, model, size)

    if not upload_server:
        print("[image_gen] 未配置上传服务器，使用普通生图")
        return generate_images(prompts, backend, model, size)

    print(f"[image_gen] 上传 {len(ref_paths)} 张参考图...")
    ref_urls = _upload_refs(ref_paths, upload_server)

    if not ref_urls:
        print("[image_gen] 参考图上传全部失败，退回普通生图")
        return generate_images(prompts, backend, model, size)

    print(f"[image_gen] 上传成功 {len(ref_urls)} 张，开始生图（含参考图）")

    total   = len(prompts)
    results = [None] * total
    q       = queue.Queue()

    threads = [
        threading.Thread(
            target=_worker,
            args=(i, p, model, size, q, ref_urls, backend),
            daemon=True
        )
        for i, p in enumerate(prompts)
    ]
    for t in threads: t.start()
    for _ in range(total):
        item = q.get()
        results[item["index"]] = item.get("url")
    for t in threads: t.join()
    return results
