# -*- coding: utf-8 -*-
"""
LLM 调用封装（复用阶段1核心，供应商可配置）。
阶段4 的 /chat SSE 接口使用 stream_llm()；Agent 循环使用 call_llm()。
"""
from .. import config


def _get_client():
    if not config.LLM_API_KEY:
        raise RuntimeError("未配置 LLM_API_KEY")
    from openai import OpenAI
    return OpenAI(api_key=config.LLM_API_KEY, base_url=config.LLM_BASE_URL)


def call_llm(messages: list[dict], temperature: float = 0.3) -> str:
    """同步调用（Agent 循环用）。"""
    client = _get_client()
    resp = client.chat.completions.create(
        model=config.LLM_MODEL, messages=messages, temperature=temperature)
    return resp.choices[0].message.content


def stream_llm(messages: list[dict], temperature: float = 0.3):
    """流式调用（SSE 打字机效果用）。"""
    client = _get_client()
    stream = client.chat.completions.create(
        model=config.LLM_MODEL, messages=messages,
        temperature=temperature, stream=True)
    for event in stream:
        delta = event.choices[0].delta
        if delta and delta.content:
            yield delta.content
