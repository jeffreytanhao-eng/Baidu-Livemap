"""TileManager：LRU 淘汰、缓存命中不重建、缺片返回 None。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import app.engine as engine_mod
from app.engine import RegionBundle, TileManager

# 网格原点 (115.96, 39.64)；三列相邻 tile
P_R0C0 = (116.00, 39.65)
P_R0C1 = (116.02, 39.65)
P_R0C2 = (116.07, 39.65)


@dataclass
class _Svc:
    _regions: dict

    def load_region(self, name: str) -> None:
        self._regions[name] = True


def _bundle(name: str) -> RegionBundle:
    return RegionBundle(
        name=name,
        center_wgs=(116.0, 39.65),
        graph=None,
        walls=[],
        water=[],
        water_labels=[],
        barrier_labels=[],
        classified=[],
        enterable=[],
    )


def _make(tmp_path, monkeypatch, max_tiles: int):
    for name in ("b6r_r00c00", "b6r_r00c01", "b6r_r00c02"):
        (tmp_path / name).mkdir()
    svc = _Svc({})
    tm = TileManager(svc, tmp_path, max_tiles=max_tiles)
    made: list[str] = []

    def fake_build(service, name, *, lite=False, extra_radius_m=0.0):
        service.load_region(name)
        made.append(name)
        return _bundle(name)

    monkeypatch.setattr(engine_mod, "_build_bundle", fake_build)
    return tm, svc, made


def test_lru_evicts_oldest_and_unloads_fabric(tmp_path, monkeypatch):
    tm, svc, made = _make(tmp_path, monkeypatch, max_tiles=2)
    b0 = asyncio.run(tm.ensure(*P_R0C0))
    b1 = asyncio.run(tm.ensure(*P_R0C1))
    b2 = asyncio.run(tm.ensure(*P_R0C2))
    assert [b.name for b in (b0, b1, b2)] == ["b6r_r00c00", "b6r_r00c01", "b6r_r00c02"]
    # 容量 2：最早的 r00c00 被淘汰，且底图从 service 卸载
    assert "b6r_r00c00" not in svc._regions
    assert "b6r_r00c02" in svc._regions
    # 再访问被淘汰的片会重建
    again = asyncio.run(tm.ensure(*P_R0C0))
    assert again.name == "b6r_r00c00"
    assert made.count("b6r_r00c00") == 2


def test_cache_hit_does_not_rebuild(tmp_path, monkeypatch):
    tm, _svc, made = _make(tmp_path, monkeypatch, max_tiles=4)
    first = asyncio.run(tm.ensure(*P_R0C1))
    second = asyncio.run(tm.ensure(*P_R0C1))
    assert first is second
    assert made == ["b6r_r00c01"]


def test_missing_tile_returns_none(tmp_path, monkeypatch):
    tm, _svc, _made = _make(tmp_path, monkeypatch, max_tiles=4)
    # 六环内但无片目录的点（网格存在、目录缺失）
    assert asyncio.run(tm.ensure(116.30, 39.70)) is None


def test_has_tile(tmp_path, monkeypatch):
    tm, _svc, _made = _make(tmp_path, monkeypatch, max_tiles=4)
    assert tm.has_tile(*P_R0C0) is True
    assert tm.has_tile(116.30, 39.70) is False
