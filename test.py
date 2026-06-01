import streamlit as st
from openai import OpenAI

# 仅保留支持 chat completions 的文本生成模型
# 已排除: embedding、reward、safety-guard、parse、reranker、vision-only、deplot、nvclip、streampetr 等特殊模型
MODELS = [
    "01-ai/yi-large",
    "abacusai/dracarys-llama-3.1-70b-instruct",
    "ai21labs/jamba-1.5-large-instruct",
    "ai21labs/jamba-1.5-mini-instruct",
    "aisingapore/sea-lion-7b-instruct",
    "baichuan-inc/baichuan2-13b-chat",
    "bigcode/starcoder2-15b",
    "bigcode/starcoder2-7b",
    "bytedance/seed-oss-36b-instruct",
    "databricks/dbrx-instruct",
    "deepseek-ai/deepseek-coder-6.7b-instruct",
    "deepseek-ai/deepseek-r1-distill-llama-8b",
    "deepseek-ai/deepseek-r1-distill-qwen-14b",
    "deepseek-ai/deepseek-r1-distill-qwen-32b",
    "deepseek-ai/deepseek-r1-distill-qwen-7b",
    "deepseek-ai/deepseek-v3.1",
    "deepseek-ai/deepseek-v3.1-terminus",
    "deepseek-ai/deepseek-v3.2",
    "google/codegemma-1.1-7b",
    "google/codegemma-7b",
    "google/gemma-2-27b-it",
    "google/gemma-2-2b-it",
    "google/gemma-2-9b-it",
    "google/gemma-2b",
    "google/gemma-3-12b-it",
    "google/gemma-3-1b-it",
    "google/gemma-3-27b-it",
    "google/gemma-3-4b-it",
    "google/gemma-3n-e2b-it",
    "google/gemma-3n-e4b-it",
    "google/gemma-7b",
    "google/recurrentgemma-2b",
    "gotocompany/gemma-2-9b-cpt-sahabatai-instruct",
    "ibm/granite-3.0-3b-a800m-instruct",
    "ibm/granite-3.0-8b-instruct",
    "ibm/granite-3.3-8b-instruct",
    "ibm/granite-34b-code-instruct",
    "ibm/granite-8b-code-instruct",
    "igenius/colosseum_355b_instruct_16k",
    "igenius/italia_10b_instruct_16k",
    "institute-of-science-tokyo/llama-3.1-swallow-70b-instruct-v0.1",
    "institute-of-science-tokyo/llama-3.1-swallow-8b-instruct-v0.1",
    "marin/marin-8b-instruct",
    "mediatek/breeze-7b-instruct",
    "meta/codellama-70b",
    "meta/llama-3.1-405b-instruct",
    "meta/llama-3.1-70b-instruct",
    "meta/llama-3.1-8b-instruct",
    "meta/llama-3.2-11b-vision-instruct",
    "meta/llama-3.2-1b-instruct",
    "meta/llama-3.2-3b-instruct",
    "meta/llama-3.2-90b-vision-instruct",
    "meta/llama-3.3-70b-instruct",
    "meta/llama-4-maverick-17b-128e-instruct",
    "meta/llama-4-scout-17b-16e-instruct",
    "meta/llama2-70b",
    "meta/llama3-70b-instruct",
    "meta/llama3-8b-instruct",
    "microsoft/phi-3-medium-128k-instruct",
    "microsoft/phi-3-medium-4k-instruct",
    "microsoft/phi-3-mini-128k-instruct",
    "microsoft/phi-3-mini-4k-instruct",
    "microsoft/phi-3-small-128k-instruct",
    "microsoft/phi-3-small-8k-instruct",
    "microsoft/phi-3.5-mini-instruct",
    "microsoft/phi-3.5-moe-instruct",
    "microsoft/phi-4-mini-flash-reasoning",
    "microsoft/phi-4-mini-instruct",
    "minimaxai/minimax-m2.1",
    "minimaxai/minimax-m2.5",
    "mistralai/codestral-22b-instruct-v0.1",
    "mistralai/devstral-2-123b-instruct-2512",
    "mistralai/magistral-small-2506",
    "mistralai/mamba-codestral-7b-v0.1",
    "mistralai/mathstral-7b-v0.1",
    "mistralai/ministral-14b-instruct-2512",
    "mistralai/mistral-7b-instruct-v0.2",
    "mistralai/mistral-7b-instruct-v0.3",
    "mistralai/mistral-large",
    "mistralai/mistral-large-2-instruct",
    "mistralai/mistral-large-3-675b-instruct-2512",
    "mistralai/mistral-medium-3-instruct",
    "mistralai/mistral-nemotron",
    "mistralai/mistral-small-24b-instruct",
    "mistralai/mistral-small-3.1-24b-instruct-2503",
    "mistralai/mixtral-8x22b-instruct-v0.1",
    "mistralai/mixtral-8x22b-v0.1",
    "mistralai/mixtral-8x7b-instruct-v0.1",
    "moonshotai/kimi-k2-instruct",
    "moonshotai/kimi-k2-instruct-0905",
    "moonshotai/kimi-k2-thinking",
    "moonshotai/kimi-k2.5",
    "nv-mistralai/mistral-nemo-12b-instruct",
    "nvidia/cosmos-reason2-8b",
    "nvidia/llama-3.1-nemotron-51b-instruct",
    "nvidia/llama-3.1-nemotron-70b-instruct",
    "nvidia/llama-3.1-nemotron-nano-4b-v1.1",
    "nvidia/llama-3.1-nemotron-nano-8b-v1",
    "nvidia/llama-3.1-nemotron-ultra-253b-v1",
    "nvidia/llama-3.3-nemotron-super-49b-v1",
    "nvidia/llama-3.3-nemotron-super-49b-v1.5",
    "nvidia/llama3-chatqa-1.5-70b",
    "nvidia/llama3-chatqa-1.5-8b",
    "nvidia/mistral-nemo-minitron-8b-8k-instruct",
    "nvidia/mistral-nemo-minitron-8b-base",
    "nvidia/nemotron-3-nano-30b-a3b",
    "nvidia/nemotron-3-super-120b-a12b",
    "nvidia/nemotron-4-340b-instruct",
    "nvidia/nemotron-4-mini-hindi-4b-instruct",
    "nvidia/nemotron-content-safety-reasoning-4b",
    "nvidia/nemotron-mini-4b-instruct",
    "nvidia/nemotron-nano-3-30b-a3b",
    "nvidia/neva-22b",
    "nvidia/nvidia-nemotron-nano-9b-v2",
    "nvidia/riva-translate-4b-instruct",
    "nvidia/riva-translate-4b-instruct-v1.1",
    "nvidia/usdcode-llama-3.1-70b-instruct",
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "opengpt-x/teuken-7b-instruct-commercial-v0.4",
    "qwen/qwen2-7b-instruct",
    "qwen/qwen2.5-7b-instruct",
    "qwen/qwen2.5-coder-32b-instruct",
    "qwen/qwen2.5-coder-7b-instruct",
    "qwen/qwen3-coder-480b-a35b-instruct",
    "qwen/qwen3-next-80b-a3b-instruct",
    "qwen/qwen3-next-80b-a3b-thinking",
    "qwen/qwen3.5-122b-a10b",
    "qwen/qwen3.5-397b-a17b",
    "qwen/qwq-32b",
    "rakuten/rakutenai-7b-chat",
    "rakuten/rakutenai-7b-instruct",
    "sarvamai/sarvam-m",
    "speakleash/bielik-11b-v2.3-instruct",
    "speakleash/bielik-11b-v2.6-instruct",
    "stepfun-ai/step-3.5-flash",
    "stockmark/stockmark-2-100b-instruct",
    "thudm/chatglm3-6b",
    "tiiuae/falcon3-7b-instruct",
    "tokyotech-llm/llama-3-swallow-70b-instruct-v0.1",
    "upstage/solar-10.7b-instruct",
    "utter-project/eurollm-9b-instruct",
    "writer/palmyra-creative-122b",
    "writer/palmyra-fin-70b-32k",
    "writer/palmyra-med-70b",
    "writer/palmyra-med-70b-32k",
    "yentinglin/llama-3-taiwan-70b-instruct",
    "z-ai/glm4.7",
    "z-ai/glm5",
    "zyphra/zamba2-7b-instruct",
]

st.title("NVIDIA NIM Chat")

api_key = st.sidebar.text_input("API Key", type="password", placeholder="nvapi-...")
model = st.sidebar.selectbox("模型", MODELS)
temperature = st.sidebar.slider("Temperature", 0.0, 1.0, 0.6)
max_tokens = st.sidebar.slider("Max Tokens", 128, 4096, 1024)
use_stream = st.sidebar.checkbox("流式输出", value=True)

if st.sidebar.button("清空对话"):
    st.session_state.messages = []

if "messages" not in st.session_state:
    st.session_state.messages = []

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
                def stream_gen(response):
                    for chunk in response:
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