"""
ComfyUI API - 极简版
只控制关键参数：图片、提示词、尺寸、帧数等
"""

import requests
import time
import uuid
import json
import shutil
from pathlib import Path
import urllib.parse
import os
from dotenv import load_dotenv

load_dotenv()

# ==================== 配置 ====================
COMFYUI_URL = os.getenv("COMFYUI_URL", "http://127.0.0.1:8188")
TIMEOUT = int(os.getenv("COMFYUI_TIMEOUT", "500"))

WORKFLOW_DIR = Path(__file__).parent / "cfui_wf"
COMFYUI_INPUT_DIR = Path(os.getenv("COMFYUI_INPUT_DIR", ""))
COMFYUI_OUTPUT_DIR = Path(os.getenv("COMFYUI_OUTPUT_DIR", ""))
CLOUD_BASE = os.getenv("COMFYUI_CLOUD_BASE", "")
WORKFLOW_ID = os.getenv("COMFYUI_WORKFLOW_ID", "")
DEFAULT_NEGATIVE = "色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，静止，整体发灰，最差质量，低质量，JPEG压缩残留，丑陋的，残缺的，多余的手指，画得不好的手部，画得不好的脸部，畸形的，毁容的，形态畸形的肢体，手指融合，静止不动的画面，杂乱的背景，三条腿，背景人很多，倒着走, censored, mosaic censoring, bar censor, pixelated, glowing, bloom, blurry, day, out of focus, low detail, bad anatomy, ugly, overexposed, underexposed, distorted face, extra limbs, cartoonish, 3d render artifacts, duplicate people, unnatural lighting, bad composition, missing shadows, low resolution, poorly textured, glitch, noise, grain, static, motionless, still frame, overall grayish, worst quality, low quality, JPEG compression artifacts, subtitles, stylized, artwork, painting, illustration, cluttered background, many people in background, three legs, walking backward, zoom out, zoom in, mouth speaking, moving mouth, talking, speaking, mute speaking, unnatural skin tone, discolored eyelid, red eyelids, red upper eyelids, no red eyeshadow, closed eyes, no wide-open innocent eyes, poorly drawn hands, extra fingers, fused fingers, poorly drawn face, deformed, disfigured, malformed limbs, thighs, fog, mist, voluminous eyelashes, blush,"
# ==================== 核心函数 ====================

def call_comfyui(workflow_json, server_url=None, timeout=None):
    """
    通用 ComfyUI 调用
    
    Args:
        workflow_json: 完整的工作流 JSON（已更新参数）
        server_url: ComfyUI 地址
        timeout: 超时时间
    
    Returns:
        本地图片路径
    """
    server_url = server_url or COMFYUI_URL
    timeout = timeout or TIMEOUT
    
    # 1. 提交任务
    client_id = str(uuid.uuid4())
    payload = {"client_id": client_id, "prompt": workflow_json}
    
    headers = {
        "Content-Type": "application/json",
        "ngrok-skip-browser-warning": "true"
    }
    
    print(f"📤 提交任务到 {server_url}...")
    response = requests.post(f"{server_url}/prompt", json=payload, headers=headers, timeout=30)
    response.raise_for_status()
    
    prompt_id = response.json().get("prompt_id")
    if not prompt_id:
        raise RuntimeError("未获取到 prompt_id")
    
    print(f"✅ 任务 ID: {prompt_id}")
    
    # 2. 轮询等待
    print(f"⏳ 等待生成...")
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        time.sleep(2)
        
        hist_url = f"{server_url}/history/{urllib.parse.quote(prompt_id)}"
        hist_resp = requests.get(hist_url, headers=headers, timeout=10)
        
        if hist_resp.status_code == 200:
            history = hist_resp.json()
            
            if prompt_id in history:
                outputs = history[prompt_id].get("outputs", {})
                
                # 找到第一个图片输出
                for node_output in outputs.values():
                    images = node_output.get("images", [])
                    if images:
                        filename = images[0]["filename"]
                        subfolder = images[0].get("subfolder", "")
                        
                        print(f"✅ 生成完成！")
                        
                        # 3. 下载图片
                        return download_image(server_url, filename, subfolder, headers)
        
        if int(time.time() - start_time) % 10 == 0:
            print(f"   等待中... ({int(time.time() - start_time)}秒)")
    
    raise TimeoutError(f"超时 {timeout} 秒")


def download_image(server_url, filename, subfolder, headers):
    """下载生成的图片"""
    filename_enc = urllib.parse.quote(filename)
    subfolder_enc = urllib.parse.quote(subfolder)
    download_url = f"{server_url}/view?filename={filename_enc}&subfolder={subfolder_enc}&type=output"
    
    print(f"⬇️ 下载图片...")
    response = requests.get(download_url, headers=headers, timeout=60)
    response.raise_for_status()
    
    # 保存到本地
    output_dir = Path("images/comfyui_output")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    local_path = output_dir / f"{uuid.uuid4().hex}.png"
    with open(local_path, "wb") as f:
        f.write(response.content)
    
    print(f"✅ 已保存: {local_path}")
    return str(local_path)


# ==================== 便捷函数（硬编码参数映射）====================

def upscale_4x(input_image_path, server_url=None):
    """
    4x 图片放大
    关键参数：input_image
    """
    # 1. 复制图片到 ComfyUI input
    input_filename = f"{uuid.uuid4().hex}.png"
    shutil.copy(input_image_path, COMFYUI_INPUT_DIR / input_filename)
    
    # 2. 加载工作流
    with open(WORKFLOW_DIR / "upscale_4x.json", 'r', encoding='utf-8') as f:
        workflow = json.load(f)
    
    # 3. 硬编码更新关键参数
    workflow["1"]["inputs"]["image"] = input_filename  # LoadImage 节点
    # workflow["4"]["inputs"]["model_name"] 保持默认 "4x-UltraSharp.pth"
    
    # 4. 调用
    return call_comfyui(workflow, server_url)


def qwen_i2i(input_image_path, prompt, width=904, height=1600, steps=4, cfg=1.0, server_url=None):
    """
    Qwen 图生图
    关键参数：input_image, prompt, width, height, steps, cfg
    """
    # 1. 复制图片
    input_filename = f"{uuid.uuid4().hex}.png"
    shutil.copy(input_image_path, COMFYUI_INPUT_DIR / input_filename)
    
    # 2. 加载工作流
    with open(WORKFLOW_DIR / "qwen_i2i.json", 'r', encoding='utf-8') as f:
        workflow = json.load(f)
    
    # 3. 硬编码更新关键参数
    workflow["12"]["inputs"]["image"] = input_filename           # LoadImage
    workflow["11"]["inputs"]["prompt"] = prompt                   # TextEncode 提示词
    workflow["20"]["inputs"]["width"] = width                     # EmptyLatent 宽度
    workflow["20"]["inputs"]["height"] = height                   # EmptyLatent 高度
    workflow["9"]["inputs"]["steps"] = steps                      # KSampler 步数
    workflow["9"]["inputs"]["cfg"] = cfg                          # KSampler CFG
    
    # 4. 调用
    return call_comfyui(workflow, server_url)

def image_to_video(input_image_path, prompt, frames=5, resolution=1024):
    """
    图生视频（云端 Wan2.2 + LightX2V）
    
    Args:
        input_image_path: 输入图片的本地路径
        prompt: 正向提示词（描述动作，如"女人对着镜子跳舞"）
        frames: 总帧数（5=81帧，约2.7秒@30fps）
        resolution: 分辨率（默认1024）
    
    Returns:
        生成视频的本地路径
    """
    
    print(f"\n{'='*60}")
    print(f"🎬 图生视频")
    print(f"{'='*60}")
    print(f"📷 输入: {input_image_path}")
    print(f"💬 提示词: {prompt}")
    print(f"🎞️ 帧数: {frames} (约 {frames*16+1} 帧)")
    print(f"📐 分辨率: {resolution}")
    
    # 1. 上传图片
    print(f"\n📤 上传图片到云端...")
    with open(input_image_path, 'rb') as f:
        upload_resp = requests.post(
            f"{CLOUD_BASE}/api/comfy/upload/image",
            files={'image': f},
            timeout=60
        )
        upload_resp.raise_for_status()
    
    upload_result = upload_resp.json()
    uploaded_filename = upload_result.get('name')
    print(f"   ✅ 上传成功: {uploaded_filename}")
    
    # 2. 构建参数
    input_values = {
        "45:image": uploaded_filename,
        "10:text": prompt,
        "111:value": frames,
        "112:value": resolution,
        "42:text": DEFAULT_NEGATIVE,
        
        # 其他默认参数
        "5:crop": "center",
        "8:clear_cache_after_n_frames": 8,
        "8:multiplier": 2,
        "8:scale_factor": 1,
        "12:aspect_ratio": "original",
        "12:proportional_width": 1,
        "12:proportional_height": 1,
        "12:fit": "letterbox",
        "12:method": "lanczos",
        "12:round_to_multiple": "16",
        "12:scale_to_side": "longest",
        "12:background_color": "#000000",
        "18:batch_size": 1,
        "21:frame_rate": 30,
        "21:loop_count": 0,
        "21:format": "video/h264-mp4",
        "21:pix_fmt": "yuv420p",
        "21:crf": 19,
        "23:expression": "a*30+1",
        "24:weight_dtype": "fp8_e4m3fn",
        "25:type": "wan",
        "25:device": "cpu",
        "30:sage_attention": "sageattn_qk_int8_pv_fp16_triton",
        "32:shift": 8,
        "34:weight_dtype": "default",
        "35:strength_model": 0.5,
        "36:sage_attention": "sageattn_qk_int8_pv_fp16_triton",
        "38:shift": 8,
        "40:strength_model": 0.5,
        "41:strength_model": 1.0,
        "43:steps": 6,
        "43:cfg": 1,
        "43:add_noise": "disable",
        "43:noise_seed": 2,
        "43:start_at_step": 3,
        "43:end_at_step": 10000,
        "43:return_with_leftover_noise": "disable",
        "44:steps": 6,
        "44:cfg": 1,
        "44:add_noise": "enable",
        "44:noise_seed": 1,
        "44:start_at_step": 0,
        "44:end_at_step": 3,
        "44:return_with_leftover_noise": "enable",
        "66:strength_model": 2.0,
        "100:strength_model": 1.0
    }
    
    # 3. 提交任务
    print(f"\n🎬 提交云端工作流...")
    payload = {
        "workflow_id": WORKFLOW_ID,
        "input_values": input_values
    }
    
    response = requests.post(
        f"{CLOUD_BASE}/api/workflow/generate",
        json=payload,
        timeout=30
    )
    response.raise_for_status()
    
    result = response.json()
    
    if not result.get('success', False):
        raise RuntimeError(f"工作流执行失败: {result.get('error', '未知错误')}")
    
    prompt_id = result.get('prompt_id')
    if not prompt_id:
        raise RuntimeError("未获取到 prompt_id")
    
    print(f"   ✅ 任务已提交: {prompt_id}")
    
    # 4. 轮询等待结果
    # 4. 轮询等待结果
    # 4. 轮询等待结果
    print(f"\n⏳ 等待生成中...")
    start_time = time.time()
    timeout = 600  # 10分钟超时
    
    loop_count = 0
    
    while time.time() - start_time < timeout:
        time.sleep(3)
        loop_count += 1
        
        # ✅ 正确的历史接口（不带 prompt_id）
        hist_url = f"{CLOUD_BASE}/api/comfy/proxy/history"
        
        # 只在前3次循环打印详细调试
        debug = (loop_count <= 3)
        
        if debug:
            print(f"\n🔍 调试 #{loop_count}:")
            print(f"   URL: {hist_url}")
        
        try:
            hist_resp = requests.get(hist_url, timeout=10)
            
            if debug:
                print(f"   状态码: {hist_resp.status_code}")
            
            if hist_resp.status_code != 200:
                if debug:
                    print(f"   ⚠️ 请求失败")
                continue
            
            history = hist_resp.json()
            
            if debug:
                print(f"   返回数据类型: {type(history)}")
                if isinstance(history, dict):
                    print(f"   历史记录数量: {len(history)}")
                    print(f"   最近的几个 ID: {list(history.keys())[:3]}")
                print(f"   查找 prompt_id: {prompt_id[:8]}...")
            
            # ✅ 在所有历史记录中查找我们的任务
            if prompt_id in history:
                task_data = history[prompt_id]
                
                if debug:
                    print(f"   ✅ 找到任务！")
                    print(f"   task_data keys: {list(task_data.keys())}")
                
                # 检查任务状态
                status = task_data.get("status", {})
                if debug and status:
                    print(f"   状态: {status}")
                
                outputs = task_data.get("outputs", {})
                
                if not outputs:
                    if debug:
                        print(f"   ⚠️ outputs 为空，任务可能还在运行...")
                    continue  # 继续等待
                
                if debug:
                    print(f"   outputs 节点数: {len(outputs)}")
                
                # 遍历所有输出节点
                for node_id, node_output in outputs.items():
                    if debug:
                        print(f"   检查节点 {node_id}: {list(node_output.keys())}")
                    
                    # ✅ 尝试查找视频输出（优先 gifs，其次 videos，最后 images）
                    video_data = None
                    video_key = None
                    
                    for key in ["gifs", "videos", "images"]:
                        if key in node_output and node_output[key]:
                            video_data = node_output[key][0]
                            video_key = key
                            break
                    
                    if video_data:
                        filename = video_data.get("filename")
                        subfolder = video_data.get("subfolder", "")
                        
                        if not filename:
                            if debug:
                                print(f"   ⚠️ 节点 {node_id} 无文件名")
                            continue
                        
                        print(f"\n✅ 生成完成！")
                        print(f"   输出类型: {video_key}")
                        print(f"   文件名: {filename}")
                        if subfolder:
                            print(f"   子目录: {subfolder}")
                        
                        # 5. 下载视频
                        print(f"\n⬇️ 下载视频...")
                        
                        filename_enc = urllib.parse.quote(filename)
                        subfolder_enc = urllib.parse.quote(subfolder) if subfolder else ""
                        
                        # ✅ 根据文档构建下载 URL
                        if subfolder:
                            download_url = f"{CLOUD_BASE}/api/comfy/view?filename={filename_enc}&subfolder={subfolder_enc}&type=output"
                        else:
                            download_url = f"{CLOUD_BASE}/api/comfy/view?filename={filename_enc}&type=output"
                        
                        print(f"   URL: {download_url}")
                        
                        download_resp = requests.get(download_url, timeout=120)
                        download_resp.raise_for_status()
                        
                        # 6. 保存到本地
                        output_dir = Path("videos/comfyui_output")
                        output_dir.mkdir(parents=True, exist_ok=True)
                        
                        # 保持原始扩展名
                        ext = Path(filename).suffix or ".mp4"
                        local_filename = f"i2v_{uuid.uuid4().hex}{ext}"
                        local_path = output_dir / local_filename
                        
                        with open(local_path, "wb") as f:
                            f.write(download_resp.content)
                        
                        file_size = local_path.stat().st_size / 1024 / 1024  # MB
                        print(f"   ✅ 已保存: {local_path}")
                        print(f"   文件大小: {file_size:.2f} MB")
                        print(f"\n{'='*60}")
                        print(f"✅ 图生视频完成！")
                        print(f"{'='*60}\n")
                        
                        return str(local_path)
                
                # 如果走到这里，说明有 outputs 但没有视频
                if debug:
                    print(f"   ⚠️ 任务存在但未找到视频输出")
                    print(f"   完整 outputs: {json.dumps(outputs, indent=2, ensure_ascii=False)[:500]}")
            
            else:
                if debug:
                    print(f"   ❌ 未找到 prompt_id，继续等待...")
        
        except Exception as e:
            if debug:
                print(f"   ⚠️ 请求异常: {type(e).__name__}: {e}")
            import traceback
            if debug:
                traceback.print_exc()
        
        # 定期打印进度（非调试模式）
        elapsed = int(time.time() - start_time)
        if elapsed % 10 == 0 and not debug:
            print(f"   等待中... ({elapsed}秒)")
    
    raise TimeoutError(f"生成超时 ({timeout}秒)")

if __name__ == "__main__":
    # 测试图生视频
    result = image_to_video(
        "test/1.png",
        prompt="女人对着镜子跳舞",
        frames=3,
        resolution=1024
    )
    print(f"结果: {result}")
