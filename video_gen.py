"""
==============================================================================
                   视频生成API统一调度系统 v1.0
==============================================================================

📚 文件说明：
本文件实现了一个视频生成API调度系统，支持PoloAI平台的Veo模型。

🎯 核心功能：
1. 统一接口：通过VideoGenerator调用
2. 自动模式检测：根据输入自动判断生成模式
3. 参数自动转换：自动处理图片URL上传

📦 支持的平台：
- PoloAI平台：支持Veo 3.1系列模型

🔧 支持的模型：
- veo_3_1: 标准模型
- veo_3_1-4K: 4K高清模型
- veo_3_1-fast: 快速模型
- veo_3_1-fast-4K: 4K快速模型

==============================================================================
"""

import os
import re
import time
import uuid
import requests
from pathlib import Path
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from dotenv import load_dotenv

load_dotenv()

# ==============================================================================
# 1. 基础配置
# ==============================================================================

# 禁用代理
os.environ['NO_PROXY'] = '*'
os.environ['no_proxy'] = '*'

# 配置全局Session
session = requests.Session()
session.trust_env = False
session.proxies = {'http': None, 'https': None}
retries = Retry(
    total=3, 
    backoff_factor=1.2, 
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET", "POST"]
)
session.mount("https://", HTTPAdapter(max_retries=retries))
session.mount("http://", HTTPAdapter(max_retries=retries))

POLOAI_API_KEY = os.getenv("POLOAI_API_KEY", "")
POLOAI_HOST = os.getenv("POLOAI_HOST", "https://poloai.top")
IMAGE_UPLOAD_SERVER = os.getenv("MANJU_IMAGE_UPLOAD_SERVER", "")

TIMEOUT = 600
POLL_INTERVAL = 15
MAX_RETRY_ATTEMPTS = 3
RETRY_DELAY = 5


# ==============================================================================
# 2. 辅助函数
# ==============================================================================

def upload_image_to_server(image_path):
    """上传图片到服务器获取URL"""
    try:
        upload_url = f"{IMAGE_UPLOAD_SERVER}/upload/image"
        
        mime_types = {
            '.png': 'image/png',
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.gif': 'image/gif',
            '.webp': 'image/webp'
        }
        ext = Path(image_path).suffix.lower()
        mime_type = mime_types.get(ext, 'image/png')
        
        with open(image_path, 'rb') as f:
            files = {'image': (Path(image_path).name, f, mime_type)}
            response = session.post(upload_url, files=files, timeout=60)
        
        response.raise_for_status()
        url = response.json().get('url')
        print(f"✅ 图片已上传: {url}")
        return url
    except Exception as e:
        raise Exception(f"图片上传失败: {e}")


def download_video(url, save_path):
    """下载视频到本地"""
    try:
        print(f"📥 下载视频: {url[:80]}...")
        response = session.get(url, timeout=120, stream=True)
        response.raise_for_status()
        
        Path(save_path).parent.mkdir(parents=True, exist_ok=True)
        
        with open(save_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        
        print(f"✅ 视频已保存: {save_path}")
        return save_path
    except Exception as e:
        raise Exception(f"视频下载失败: {e}")


# ==============================================================================
# 3. PoloAI Veo API
# ==============================================================================

class PoloAIVeoAPI:
    """PoloAI平台Veo视频生成API"""
    
    def __init__(self, api_key=None):
        self.api_key = api_key or POLOAI_API_KEY
        self.host = POLOAI_HOST
        print(f"📍 [PoloAI] 初始化完成")
    
    def generate(self, prompt, model="veo_3_1", size="720x1280", 
                images=None, save_path=None):
        """生成视频（仅支持文生视频和参考图生视频）"""
        # 确定模式
        mode = "text2video" if not images else "reference_image"
        
        print(f"\n🎬 [PoloAI] 开始生成")
        print(f"   模型: {model}")
        print(f"   模式: {mode}")
        print(f"   提示词: {prompt[:100]}...")
        print(f"   尺寸: {size}")
        print(f"   图片: {len(images) if images else 0} 张")
        
        # 构建请求
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        
        content = [{"type": "text", "text": prompt}]
        if images:
            for url in images:
                content.append({
                    "type": "image_url",
                    "image_url": {"url": url}
                })
        
        payload = {
            "model": model,
            "stream": False,
            "size": size,
            "messages": [{"role": "user", "content": content}]
        }
        
        # 提交任务
        print(f"📤 [PoloAI] 提交任务...")
        response = session.post(
            f"{self.host}/v1/chat/completions",
            json=payload,
            headers=headers,
            timeout=600
        )
        response.raise_for_status()
        result = response.json()
        
        if "error" in result:
            raise Exception(f"API错误: {result['error']}")
        
        # 提取任务ID或视频URL
        task_id, video_url = self._extract_info(result)
        
        if video_url:
            # 立即完成
            print(f"✅ [PoloAI] 视频已生成")
        elif task_id:
            # 异步任务
            print(f"✅ [PoloAI] 任务ID: {task_id}")
            video_url = self._poll_result(task_id)
        else:
            raise Exception("未获取到任务ID或视频URL")
        
        # 下载视频
        if not save_path:
            save_path = f"temp_{uuid.uuid4().hex[:8]}.mp4"
        
        return download_video(video_url, save_path)
    
    def _extract_info(self, response):
        """提取任务ID和视频URL"""
        try:
            content = response["choices"][0]["message"]["content"]
            
            # 先尝试提取视频URL（立即完成）
            video_match = re.search(r'https?://[^\s\)\]]+?\.mp4', content)
            if video_match:
                return None, video_match.group(0)
            
            # 否则提取任务ID（异步任务）
            task_match = re.search(r"[A-Za-z0-9._-]+:[A-Za-z0-9_-]+", content)
            if task_match:
                return task_match.group(0), None
            
            return None, None
        except:
            return None, None
    
    def _poll_result(self, task_id):
        """轮询任务结果"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json"
        }
        
        start_time = time.time()
        
        while True:
            if time.time() - start_time > TIMEOUT:
                raise TimeoutError(f"生成超时（{TIMEOUT}秒）")
            
            # 带重试的请求
            for attempt in range(MAX_RETRY_ATTEMPTS):
                try:
                    response = session.get(
                        f"{self.host}/v1/videos/{task_id}",
                        headers=headers,
                        timeout=60
                    )
                    response.raise_for_status()
                    data = response.json()
                    break
                except Exception as e:
                    if attempt < MAX_RETRY_ATTEMPTS - 1:
                        print(f"⚠️ [PoloAI] 请求失败，{RETRY_DELAY}秒后重试...")
                        time.sleep(RETRY_DELAY)
                    else:
                        raise
            
            status = data.get("status")
            progress = data.get("progress", 0)
            
            print(f"⏳ [PoloAI] 进度: {progress}% | 状态: {status}")
            
            if status == "completed":
                video_url = data.get("video_url")
                if not video_url:
                    raise Exception("未获取到视频URL")
                print(f"✅ [PoloAI] 生成完成")
                return video_url
            
            elif status == "failed":
                raise Exception(f"生成失败")
            
            time.sleep(POLL_INTERVAL)


# ==============================================================================
# 4. 统一接口
# ==============================================================================

class VideoGenerator:
    """
    统一视频生成器
    
    使用方式:
        gen = VideoGenerator()
        
        # 文生视频
        video_path = gen.generate(prompt="a cat")
        
        # 参考图生视频（1-3张图片）
        video_path = gen.generate(prompt="...", images=["ref1.png"])
        video_path = gen.generate(prompt="...", images=["ref1.png", "ref2.png"])
        video_path = gen.generate(prompt="...", images=["ref1.png", "ref2.png", "ref3.png"])
    """
    
    def __init__(self, api_key=None):
        self.api = PoloAIVeoAPI(api_key=api_key)
        print(f"\n{'='*60}")
        print(f"🎬 视频生成器初始化完成")
        print(f"{'='*60}")
    
    def generate(self, prompt, model="veo_3_1-fast", size="720x1280", 
                images=None, save_path=None, max_retries=3):
        """
        生成视频（带自动重试）
        
        Args:
            prompt: 提示词
            model: 模型 (veo_3_1, veo_3_1-4K, veo_3_1-fast, veo_3_1-fast-4K)
            size: 尺寸 (720x1280 或 1280x720)
            images: 图片列表（本地路径或URL，支持1-3张）
            save_path: 保存路径
            max_retries: 最大重试次数
        
        Returns:
            str: 视频本地路径
        """
        # 处理图片：本地路径上传获取URL
        image_urls = None
        if images:
            image_urls = []
            for img in images:
                if img.startswith("http"):
                    image_urls.append(img)
                else:
                    url = upload_image_to_server(img)
                    image_urls.append(url)
        
        # 重试逻辑
        for attempt in range(max_retries):
            try:
                return self.api.generate(
                    prompt=prompt,
                    model=model,
                    size=size,
                    images=image_urls,
                    save_path=save_path
                )
            except Exception as e:
                if "503" in str(e) and attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 30
                    print(f"⚠️ 服务暂时不可用，{wait_time}秒后重试...")
                    time.sleep(wait_time)
                else:
                    raise


# ==============================================================================
# 5. 导出接口
# ==============================================================================

__all__ = [
    'VideoGenerator',
    'PoloAIVeoAPI',
    'upload_image_to_server',
    'download_video'
]


