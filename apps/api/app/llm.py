"""可选 LLM 诊断开关（默认关闭，缺省 100% 走模板诊断）。

配置环境变量 ``LLM_API_KEY`` + ``LLM_ENDPOINT`` 后，:func:`diagnose` 尝试调用
兼容 OpenAI Chat Completions 的 HTTP 接口（如火山方舟）生成白话诊断。
``LLM_ENDPOINT`` 填 base_url（自动拼 ``/chat/completions``）或完整 URL 均可；
未配置、超时或返回格式不符时一律返回 ``None``，由编排层回退到模板诊断——
LLM 只是可选增强，主流程不依赖它，也不允许它阻塞出圈。
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger("livemap.llm")

# 连接 5s，读超时放宽到 45s：思考型模型（未禁用思考时）生成 600 token 可能要 30s+
_TIMEOUT = httpx.Timeout(45.0, connect=5.0)
MAX_LINES = 6
MAX_TOKENS = 600

SYSTEM_PROMPT = (
    "你是社区步行生活圈体检报告的撰稿人。根据用户给出的体检事实（JSON），"
    "用中文白话写 3-6 条诊断结论，每条独占一行。写作要求：\n"
    "1. 每条按「结论 + 数字依据 + 一句可执行建议」组织，"
    "例如：「医疗养老暂未达标，最近的社区卫生服务中心步行约 22 分钟，"
    "超出 15 分钟目标，建议优先关注东侧片区配套」；\n"
    "2. 用自然句子陈述，不得出现 JSON 字段名、表格符号或行首编号；\n"
    "3. 涉及最差方向的条目，行首必须加「sector:方向 」前缀（如「sector:西南 」，"
    "后面跟正文），前端会把方向渲染成可点击徽标；\n"
    "4. areaRatio 低于 0.3 时，用一句话解释路网约束下实际可达范围"
    "明显小于直线直觉范围；\n"
    "5. 全部为数据可支撑的判断，不要臆造设施数量与名称，"
    "只输出句子本身，不要开场白与总结。"
)


def llm_configured() -> bool:
    """两个环境变量都非空才算开启。"""
    return bool(
        (os.environ.get("LLM_API_KEY") or "").strip()
        and (os.environ.get("LLM_ENDPOINT") or "").strip()
    )


def _chat_url(endpoint: str) -> str:
    """兼容两种填法：base_url（自动拼 /chat/completions）或完整 URL。"""
    endpoint = endpoint.rstrip("/")
    if endpoint.endswith("chat/completions"):
        return endpoint
    return f"{endpoint}/chat/completions"


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


async def _post(
    client: httpx.AsyncClient, url: str, payload: dict, api_key: str
) -> dict:
    resp = await client.post(
        url, json=payload, headers={"Authorization": f"Bearer {api_key}"}
    )
    resp.raise_for_status()
    return resp.json()


async def diagnose(facts: dict[str, Any]) -> list[str] | None:
    """根据体检事实生成白话诊断。

    未配置 LLM 或调用失败时返回 ``None``（调用方回退模板诊断）。
    """
    if not llm_configured():
        return None
    endpoint = (os.environ.get("LLM_ENDPOINT") or "").strip()
    api_key = (os.environ.get("LLM_API_KEY") or "").strip()
    # thinking disabled：跳过思考型模型的推理阶段（实测 26s -> 5s）；
    # 非思考模型若不认识该参数返回 4xx，则去掉后重试一次
    payload = {
        "model": os.environ.get("LLM_MODEL") or "default",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": "体检事实（JSON）：\n"
                + json.dumps(facts, ensure_ascii=False, default=str),
            },
        ],
        "max_tokens": MAX_TOKENS,
        "temperature": 0.4,
        "thinking": {"type": "disabled"},
    }
    url = _chat_url(endpoint)
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            try:
                data = await _post(client, url, payload, api_key)
            except httpx.HTTPStatusError:
                payload.pop("thinking", None)
                data = await _post(client, url, payload, api_key)
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning(
            "llm diagnose failed (%s: %s), fallback to template",
            type(exc).__name__,
            exc,
        )
        return None
    lines = _parse_lines(data)
    if lines is None:
        logger.warning("llm diagnose returned unexpected format, fallback to template")
    return lines
