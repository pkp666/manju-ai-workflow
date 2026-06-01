import json
import re
from llm import call_llm
from prompts import STYLE_CONFIG_SYSTEM, STYLE_CONFIG_USER, STYLE_OPTIONS


def parse_style_from_text(user_input: str) -> dict:
    """用户自由描述 → AI解析为3层结构"""
    prompt = STYLE_CONFIG_USER.format(user_input=user_input)
    result = call_llm(prompt, STYLE_CONFIG_SYSTEM)
    return _extract_json(result)


def build_style_from_selections(media_type: str, art_style: dict, color_tone: dict) -> dict:
    """
    3层点选结果 → 组装 style_config
    """
    media = next((o for o in STYLE_OPTIONS["media_type"] if o["label"] == media_type), None)
    suffix_parts = []
    if media:
        suffix_parts.append(media["en"])
    if art_style:
        suffix_parts.append(art_style["en"])
    if color_tone:
        suffix_parts.append(color_tone["en"])

    return {
        "media_type": {"label": media_type, "en": media["en"] if media else ""},
        "art_style":  art_style,
        "color_tone": color_tone,
        "suffix":     ", ".join(suffix_parts)
    }


def get_art_styles(media_type: str) -> list:
    """根据媒介类型返回对应的风格流派列表"""
    return STYLE_OPTIONS["art_style"].get(media_type, [])


def get_options() -> dict:
    return STYLE_OPTIONS


def _extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```[a-z]*\n?", "", text)
    text = re.sub(r"\n?```$", "", text)
    text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"未找到JSON:\n{text[:200]}")
    return json.loads(text[start:end + 1])