"""SQLite 小区数据存取：表结构、初始化与查询（stdlib sqlite3，不引 ORM）。

数据由本地导入流程写入；坐标统一 WGS84。小区量级（示范区域数百~数千行）直接全表内存匹配即可。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from geo import haversine_m

_SCHEMA = """
CREATE TABLE IF NOT EXISTS communities (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  name_norm TEXT NOT NULL,
  lng REAL NOT NULL,
  lat REAL NOT NULL,
  district TEXT,
  bizcircle TEXT,
  sale_avg REAL,
  rent1_min REAL, rent1_max REAL,
  rent2_min REAL, rent2_max REAL,
  rent3_min REAL, rent3_max REAL,
  photo_path TEXT,
  intro TEXT,
  source TEXT,
  source_url TEXT,
  fetched_at TEXT,
  score REAL,
  scored_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_communities_norm ON communities(name_norm);
CREATE INDEX IF NOT EXISTS idx_communities_geo ON communities(lng, lat);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    # 应用级共享连接：路由在线程池中执行，需允许跨线程使用（此处仅读）
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: str | Path) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as conn:
        conn.executescript(_SCHEMA)
        # 存量库迁移：communities 表补 score/scored_at 列（PRAGMA 逐列检查，幂等）
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(communities)").fetchall()}
        if "score" not in cols:
            conn.execute("ALTER TABLE communities ADD COLUMN score REAL")
        if "scored_at" not in cols:
            conn.execute("ALTER TABLE communities ADD COLUMN scored_at TEXT")


def normalize_name(name: str) -> str:
    """名称规范化：去空白与括号字符（「劲松（一期）」与「劲松一期」归一）。"""
    out: list[str] = []
    for ch in str(name or "").strip().lower():
        if ch.isspace():
            continue
        if ch in "（）()[]【】「」『』·、,，。.-—_":
            continue
        out.append(ch)
    return "".join(out)


def _row_to_community(row: sqlite3.Row) -> dict:
    def _rent(key: str) -> dict | None:
        lo, hi = row[f"rent{key}_min"], row[f"rent{key}_max"]
        if lo is None and hi is None:
            return None
        return {"min": lo, "max": hi}

    return {
        "id": row["id"],
        "name": row["name"],
        "lng": row["lng"],
        "lat": row["lat"],
        "district": row["district"],
        "bizcircle": row["bizcircle"],
        "saleAvg": row["sale_avg"],
        "rent": {"l1": _rent("1"), "l2": _rent("2"), "l3": _rent("3")},
        "photoUrl": (
            "/static/uploads/" + row["photo_path"].removeprefix("uploads/")
            if row["photo_path"]
            else None
        ),
        "intro": row["intro"],
    }


def find_by_name(conn: sqlite3.Connection, name: str) -> dict | None:
    """名称精确匹配：norm 相等优先，其次互相包含（如「五建小区」vs「五建」）。"""
    norm = normalize_name(name)
    if not norm:
        return None
    row = conn.execute(
        "SELECT * FROM communities WHERE name_norm = ? ORDER BY id LIMIT 1", (norm,)
    ).fetchone()
    if row is not None:
        return _row_to_community(row)
    rows = conn.execute("SELECT * FROM communities").fetchall()
    for row in rows:
        other = row["name_norm"]
        if (norm in other or other in norm) and min(len(norm), len(other)) >= 4:
            return _row_to_community(row)
    return None


def find_nearest(
    conn: sqlite3.Connection, lng: float, lat: float, max_m: float = 500.0
) -> tuple[dict, float] | None:
    """坐标最近匹配：haversine 全表扫描（量级小），超 max_m 视为未命中。"""
    rows = conn.execute("SELECT * FROM communities").fetchall()
    best: tuple[sqlite3.Row, float] | None = None
    for row in rows:
        d = haversine_m(lng, lat, row["lng"], row["lat"])
        if best is None or d < best[1]:
            best = (row, d)
    if best is None or best[1] > max_m:
        return None
    return _row_to_community(best[0]), best[1]


def find_nearby(
    conn: sqlite3.Connection, lng: float, lat: float, radius_m: float = 2000.0
) -> list[tuple[dict, float]]:
    """半径内小区列表：haversine 全表扫描，distanceM 升序。"""
    out: list[tuple[dict, float]] = []
    for row in conn.execute("SELECT * FROM communities").fetchall():
        d = haversine_m(lng, lat, row["lng"], row["lat"])
        if d <= radius_m:
            out.append((_row_to_community(row), d))
    out.sort(key=lambda pair: pair[1])
    return out


def update_score(conn: sqlite3.Connection, community_id: int, score: float) -> None:
    """体检得分回写：记录得分与打分时间（UTC ISO）。长期共享连接需显式提交。"""
    conn.execute(
        "UPDATE communities SET score=?, scored_at=? WHERE id=?",
        (float(score), datetime.now(timezone.utc).isoformat(timespec="seconds"), community_id),
    )
    conn.commit()


def upsert_community(conn: sqlite3.Connection, item: dict) -> int:
    """导入写入：norm 相同且坐标 <300m 视为同小区，更新而非重复插入。"""
    norm = normalize_name(item["name"])
    existing = conn.execute(
        "SELECT id, lng, lat FROM communities WHERE name_norm = ?", (norm,)
    ).fetchone()
    if existing is not None:
        d = haversine_m(item["lng"], item["lat"], existing["lng"], existing["lat"])
        if d < 300.0:
            conn.execute(
                """UPDATE communities SET sale_avg=?, rent1_min=?, rent1_max=?, rent2_min=?,
                   rent2_max=?, rent3_min=?, rent3_max=?, photo_path=?, intro=?, source=?,
                   source_url=?, fetched_at=? WHERE id=?""",
                (
                    item.get("sale_avg"),
                    item.get("rent1_min"), item.get("rent1_max"),
                    item.get("rent2_min"), item.get("rent2_max"),
                    item.get("rent3_min"), item.get("rent3_max"),
                    item.get("photo_path"), item.get("intro"), item.get("source"),
                    item.get("source_url"), item.get("fetched_at"), existing["id"],
                ),
            )
            return existing["id"]
    cur = conn.execute(
        """INSERT INTO communities (name, name_norm, lng, lat, district, bizcircle, sale_avg,
           rent1_min, rent1_max, rent2_min, rent2_max, rent3_min, rent3_max, photo_path,
           intro, source, source_url, fetched_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            item["name"], norm, item["lng"], item["lat"], item.get("district"),
            item.get("bizcircle"), item.get("sale_avg"),
            item.get("rent1_min"), item.get("rent1_max"),
            item.get("rent2_min"), item.get("rent2_max"),
            item.get("rent3_min"), item.get("rent3_max"),
            item.get("photo_path"), item.get("intro"), item.get("source"),
            item.get("source_url"), item.get("fetched_at"),
        ),
    )
    return int(cur.lastrowid)
