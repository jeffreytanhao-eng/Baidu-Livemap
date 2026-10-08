"""可选 LLM 诊断开关（默认关闭，缺省 100% 走模板诊断）。

配置环境变量 ``LLM_API_KEY`` + ``LLM_ENDPOINT`` 后，:func:`diagnose` 尝试调用
兼容 OpenAI Chat Completions 的 HTTP 接口生成白话诊断；未配置、超时或返回
格式不符时一律返回 ``None``，由编排层回退到模板诊断——LLM 只是可选增强，
主流程不依赖它，也不允许它阻塞出圈。
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger("livemap.llm")

TIMEOUT_S = 5.0
MAX_LINES = 6


def llm_configured() -> bool:
    """两个环境变量都非空才算开启。"""
    return bool(
        (os.environ.get("LLM_API_KEY") or "").strip()
        and (os.environ.get("LLM_ENDPOINT") or "").strip()
    )


def _parse_lines(data: dict) -> list[str] | None:
    """从 Chat Completions 响应提取 3-6 行非空文本；格式不符返回 None。"""
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None
    if not isinstance(content, str):
        return None
    lines = [ln.strip(" -•\t") for ln in content.splitlines()]
    lines = [ln for ln in lines if ln]
    if not lines:
        return None
    return lines[:MAX_LINES]


async def diagnose(facts: dict[str, Any]) -> list[str] | None:
    """根据体检事实生成白话诊断。

    未配置 LLM 或调用失败时返回 ``None``（调用方回退模板诊断）。
    """
    if not llm_configured():
        return None
    endpoint = (os.environ.get("LLM_ENDPOINT") or "").strip()
    api_key = (os.environ.get("LLM_API_KEY") or "").strip()
    prompt = (
        "你是社区规划助手。根据以下 15 分钟生活圈体检数据（JSON），"
        "用 3-6 句中文白话指出缺口与原因，只输出句子，每句一行：\n"
        f"{facts!r}"
    )
    payload = {
        "model": os.environ.get("LLM_MODEL") or "default",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 400,
    }
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
            resp = await client.post(
                endpoint,
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
            )
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("llm diagnose failed, fallback to template: %s", exc)
        return None
    lines = _parse_lines(data)
    if lines is None:
        logger.warning("llm diagnose returned unexpected format, fallback to template")
    return lines
