import os
import time
import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY  = os.getenv("YUNWU_API_KEY", "")
BASE_URL = os.getenv("YUNWU_CHAT_BASE_URL", "https://yunwu.ai/v1/chat/completions")
MODEL_ID = os.getenv("YUNWU_TEXT_MODEL", "gpt-5.1")

# (连接超时, 读取超时)
TIMEOUT = (40, 300)

# 重试配置
MAX_RETRIES = 4        # 最多重试4次（加上第1次共5次）
RETRY_DELAY = 2        # 初始等待2秒，指数退避


def call_llm(user_prompt: str, system_prompt: str = "你是一个专业助手") -> str:
    """
    调用LLM，返回字符串结果。
    SSL错误/连接错误自动重试，指数退避。
    出错超过重试次数时抛出异常，由调用方处理。
    """
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": MODEL_ID,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        "stream": False,
    }

    last_error = None

    for attempt in range(MAX_RETRIES + 1):
        try:
            response = requests.post(
                BASE_URL,
                json=payload,
                headers=headers,
                timeout=TIMEOUT,
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]

        except requests.exceptions.SSLError as e:
            last_error = e
            if attempt < MAX_RETRIES:
                wait = RETRY_DELAY * (2 ** attempt)  # 2s, 4s, 8s, 16s
                print(f"[llm] SSL错误，第{attempt+1}次，{wait}s后重试：{e}")
                time.sleep(wait)
            else:
                print(f"[llm] SSL错误，已重试{MAX_RETRIES}次，放弃")

        except requests.exceptions.ConnectionError as e:
            last_error = e
            if attempt < MAX_RETRIES:
                wait = RETRY_DELAY * (2 ** attempt)
                print(f"[llm] 连接错误，第{attempt+1}次，{wait}s后重试：{e}")
                time.sleep(wait)
            else:
                print(f"[llm] 连接错误，已重试{MAX_RETRIES}次，放弃")

        except requests.exceptions.Timeout as e:
            last_error = e
            if attempt < MAX_RETRIES:
                wait = RETRY_DELAY * (2 ** attempt)
                print(f"[llm] 超时，第{attempt+1}次，{wait}s后重试：{e}")
                time.sleep(wait)
            else:
                print(f"[llm] 超时，已重试{MAX_RETRIES}次，放弃")

        except Exception as e:
            # 其他错误（如4xx/5xx）不重试，直接抛出
            raise

    raise last_error
