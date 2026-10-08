"""整响应磁盘缓存：key = 中心坐标（约 1m 精度）+ minutes + engine，JSON 落盘，TTL 24h。"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("livemap.cache")


def disk_cache_key(lng: float, lat: float, minutes: int, engine: str) -> str:
    """中心坐标必须进键：响应内嵌 meta.center / 等时圈 / POI，中心不同结果就不同。

    坐标取整到 1e-5 度（纬度约 1.1m、经度约 0.85m），细于 OSM 路网节点间距，
    可保证「换个地点再体验」不会误命中上一处的结果。
    """
    return f"{round(lng * 1e5):d}_{round(lat * 1e5):d}_{minutes}_{engine}"


class DiskCache:
    def __init__(self, root: Path, ttl_s: float = 24 * 3600.0) -> None:
        self.root = Path(root)
        self.ttl_s = ttl_s

    def _path(self, key: str) -> Path:
        safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in key)
        return self.root / f"{safe}.json"

    def get(self, key: str) -> dict[str, Any] | None:
        path = self._path(key)
        try:
            if not path.exists():
                return None
            if time.time() - path.stat().st_mtime > self.ttl_s:
                return None
            with path.open(encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError) as exc:  # 缓存损坏视为未命中
            logger.warning("disk cache read failed: %s", exc)
            return None

    def set(self, key: str, payload: dict[str, Any]) -> None:
        path = self._path(key)
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            with tmp.open("w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)
            tmp.replace(path)
        except OSError as exc:
            logger.warning("disk cache write failed: %s", exc)
