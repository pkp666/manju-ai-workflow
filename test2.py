import streamlit as st
import requests
import base64
from openai import OpenAI

# ── 模型分类（只保留免费 Free Endpoint + 大参数强模型）──────────────────────────

# 文本对话：纯 chat completions
TEXT_MODELS = {
    "🧠 旗舰推理": [
        "nvidia/llama-3.1-nemotron-ultra-253b-v1",
        "nvidia/nemotron-3-super-120b-a12b",
        "nvidia/llama-3.3-nemotron-super-49b-v1",
        "nvidia/llama-3.3-nemotron-super-49b-v1.5",
    ],
    "💬 综合对话": [
        "meta/llama-3.1-405b-instruct",
        "meta/llama-3.3-70b-instruct",
        "meta/llama-4-maverick-17b-128e-instruct",
        "meta/llama-4-scout-17b-16e-instruct",
        "minimaxai/minimax-m2.1",
        "stepfun-ai/step-3.5-flash",
        "moonshotai/kimi-k2-instruct",
        "moonshotai/kimi-k2-thinking",
        "moonshotai/kimi-k2.5",
        "z-ai/glm4.7",
        "mistralai/mistral-large",
        "mistralai/mistral-large-3-675b-instruct-2512",
        "mistralai/mixtral-8x22b-instruct-v0.1",
        "databricks/dbrx-instruct",
    ],
    "🔢 数学/推理": [
        "deepseek-ai/deepseek-r1-distill-qwen-32b",
        "deepseek-ai/deepseek-r1-distill-qwen-14b",
        "qwen/qwq-32b",
        "qwen/qwen3.5-122b-a10b",
        "microsoft/phi-4-mini-flash-reasoning",
        "nvidia/llama-3.1-nemotron-70b-instruct",
        "nvidia/llama-3.1-nemotron-51b-instruct",
    ],
    "💻 代码生成": [
        "deepseek-ai/deepseek-v3.2",
        "deepseek-ai/deepseek-v3.1",
        "qwen/qwen2.5-coder-32b-instruct",
        "qwen/qwen3-coder-480b-a35b-instruct",
        "mistralai/codestral-22b-instruct-v0.1",
        "mistralai/devstral-2-123b-instruct-2512",
        "ibm/granite-34b-code-instruct",
        "z-ai/glm5",
    ],
    "🌏 中文优化": [
        "deepseek-ai/deepseek-v3.2",
        "qwen/qwen3.5-122b-a10b",
        "qwen/qwen2.5-7b-instruct",
        "z-ai/glm4.7",
        "z-ai/glm5",
        "baichuan-inc/baichuan2-13b-chat",
        "thudm/chatglm3-6b",
    ],
    "✍️ 创意写作": [
        "writer/palmyra-creative-122b",
        "writer/palmyra-fin-70b-32k",
        "mistralai/magistral-small-2506",
        "moonshotai/kimi-k2-instruct",
    ],
}

# 图像理解：支持 image_url 的 VLM（chat completions，消息中可传图）
VISION_MODELS = [
    "meta/llama-3.2-90b-vision-instruct",
    "meta/llama-3.2-11b-vision-instruct",
    "qwen/qwen3.5-397b-a17b",          # 最新 VLM 旗舰
    "nvidia/llama-3.1-nemotron-nano-vl-8b-v1",
    "nvidia/nemotron-nano-12b-v2-vl",
    "microsoft/phi-3.5-vision-instruct",
    "microsoft/phi-4-multimodal-instruct",
]

# 图像生成：text-to-image，使用 /v1/images/generations 接口
IMAGE_GEN_MODELS = [
    "stabilityai/stable-diffusion-xl",
    "stabilityai/stable-diffusion-3-medium",
    "black-forest-labs/FLUX.1-dev",
    "black-forest-labs/FLUX.1-schnell",
]

# ── UI ──────────────────────────────────────────────────────────────────────

st.title("NVIDIA NIM Chat")
st.caption("Free Endpoint 模型 · 分类整理版")

# 侧边栏
api_key = st.sidebar.text_input("API Key", type="password", placeholder="nvapi-...")

mode = st.sidebar.radio("模式", ["💬 文本对话", "🖼 图像理解（VLM）", "🎨 图像生成"])

if mode == "💬 文本对话":
    category = st.sidebar.selectbox("分类", list(TEXT_MODELS.keys()))
    model = st.sidebar.selectbox("模型", TEXT_MODELS[category])
    temperature = st.sidebar.slider("Temperature", 0.0, 1.0, 0.6)
    max_tokens = st.sidebar.slider("Max Tokens", 256, 4096, 1024)
    use_stream = st.sidebar.checkbox("流式输出", value=True)

elif mode == "🖼 图像理解（VLM）":
    model = st.sidebar.selectbox("模型", VISION_MODELS)
    temperature = st.sidebar.slider("Temperature", 0.0, 1.0, 0.2)
    max_tokens = st.sidebar.slider("Max Tokens", 256, 2048, 512)

elif mode == "🎨 图像生成":
    model = st.sidebar.selectbox("模型", IMAGE_GEN_MODELS)
    img_width = st.sidebar.selectbox("宽度", [512, 768, 1024], index=2)
    img_height = st.sidebar.selectbox("高度", [512, 768, 1024], index=2)
    num_steps = st.sidebar.slider("Steps", 10, 50, 30)

if st.sidebar.button("清空对话"):
    st.session_state.messages = []
    st.rerun()

if "messages" not in st.session_state:
    st.session_state.messages = []

# ── 文本对话 ──────────────────────────────────────────────────────────────────

if mode == "💬 文本对话":
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.write(msg["content"])

    if prompt := st.chat_input("输入消息"):
        if not api_key:
            st.error("请输入 API Key")
            st.stop()

        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.write(prompt)

        client = OpenAI(base_url="https://integrate.api.nvidia.com/v1", api_key=api_key)

        with st.chat_message("assistant"):
            try:
                if use_stream:
                    response = client.chat.completions.create(
                        model=model,
                        messages=st.session_state.messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        stream=True,
                    )
                    def stream_gen(r):
                        for chunk in r:
                            if chunk.choices and chunk.choices[0].delta.content:
                                yield chunk.choices[0].delta.content
                    result = st.write_stream(stream_gen(response))
                else:
                    response = client.chat.completions.create(
                        model=model,
                        messages=st.session_state.messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                    )
                    result = response.choices[0].message.content
                    st.write(result)

                st.session_state.messages.append({"role": "assistant", "content": result})
            except Exception as e:
                st.error(f"请求失败: {e}")

# ── 图像理解 ──────────────────────────────────────────────────────────────────

elif mode == "🖼 图像理解（VLM）":
    st.info("上传图片或输入图片 URL，然后提问。")

    img_source = st.radio("图片来源", ["上传图片", "图片 URL"], horizontal=True)

    image_url = None
    if img_source == "上传图片":
        uploaded = st.file_uploader("上传图片", type=["jpg", "jpeg", "png", "webp"])
        if uploaded:
            st.image(uploaded, width=400)
            b64 = base64.b64encode(uploaded.read()).decode()
            ext = uploaded.type  # e.g. image/jpeg
            image_url = f"data:{ext};base64,{b64}"
    else:
        image_url = st.text_input("图片 URL", placeholder="https://...")
        if image_url:
            st.image(image_url, width=400)

    question = st.text_area("提问", placeholder="描述这张图片 / 图中有什么文字？")

    if st.button("发送") and question:
        if not api_key:
            st.error("请输入 API Key")
            st.stop()
        if not image_url:
            st.error("请提供图片")
            st.stop()

        client = OpenAI(base_url="https://integrate.api.nvidia.com/v1", api_key=api_key)
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": image_url}},
                    {"type": "text", "text": question},
                ],
            }
        ]
        with st.spinner("分析中…"):
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                st.success(response.choices[0].message.content)
            except Exception as e:
                st.error(f"请求失败: {e}")

# ── 图像生成 ──────────────────────────────────────────────────────────────────

elif mode == "🎨 图像生成":
    st.info("输入提示词，生成图片。使用 NVIDIA NIM 图像生成 API。")

    prompt = st.text_area("提示词 (英文效果更好)", placeholder="A futuristic city at night, neon lights, 8k, photorealistic")
    negative = st.text_input("负向提示词（可选）", placeholder="blurry, low quality, watermark")

    if st.button("生成图片"):
        if not api_key:
            st.error("请输入 API Key")
            st.stop()
        if not prompt:
            st.error("请输入提示词")
            st.stop()

        with st.spinner("生成中，请稍候…"):
            try:
                client = OpenAI(base_url="https://integrate.api.nvidia.com/v1", api_key=api_key)
                body = {
                    "model": model,
                    "prompt": prompt,
                    "negative_prompt": negative or "",
                    "width": img_width,
                    "height": img_height,
                    "num_inference_steps": num_steps,
                    "response_format": "b64_json",
                }
                response = requests.post(
                    "https://integrate.api.nvidia.com/v1/images/generations",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                    timeout=120,
                )
                response.raise_for_status()
                data = response.json()
                b64_img = data["data"][0]["b64_json"]
                img_bytes = base64.b64decode(b64_img)
                st.image(img_bytes, caption=prompt[:80])
                st.download_button("下载图片", img_bytes, file_name="generated.png", mime="image/png")
            except Exception as e:
                st.error(f"生成失败: {e}")