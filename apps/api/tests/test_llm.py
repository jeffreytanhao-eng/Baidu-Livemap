"""LLM 可选开关：未配置时 diagnose 返回 None（编排层回退模板诊断）。"""

from __future__ import annotations

import asyncio

from app.llm import diagnose, llm_configured


def test_diagnose_returns_none_when_not_configured(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_ENDPOINT", raising=False)
    assert llm_configured() is False
    assert asyncio.run(diagnose({"minutes": 15, "score": 80.0})) is None
