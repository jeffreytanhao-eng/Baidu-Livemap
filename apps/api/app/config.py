"""运行配置：从环境变量 / 仓库根目录 .env 读取。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[3]

CACHE_TTL_S = 24 * 3600.0


@dataclass
class Settings:
    baidu_ak: str | None
    demo_mode: bool
    cache_dir: Path
    fabric_root: Path
    samples_dir: Path
    cache_ttl_s: float = CACHE_TTL_S
    # SQLite 小区数据（本地导入流程写入）
    db_path: Path = REPO_ROOT / "data" / "livemap.db"
    # 本地自托管图片根目录（小区照片等），由 /static/uploads 挂载
    uploads_dir: Path = REPO_ROOT / "data" / "uploads"
    # 重点学校/三甲医院名单（checkup POI 打标用）
    hot_facilities_path: Path = REPO_ROOT / "data" / "hot_facilities.json"


def load_settings() -> Settings:
    load_dotenv(REPO_ROOT / ".env")
    ak = (os.environ.get("BAIDU_SERVER_AK") or "").strip() or None
    demo_mode = (os.environ.get("DEMO_MODE") or "").strip().lower() in ("1", "true", "yes", "on")
    cache_dir = Path(os.environ.get("CACHE_DIR") or str(REPO_ROOT / ".cache"))
    if not cache_dir.is_absolute():
        cache_dir = REPO_ROOT / cache_dir
    fabric_root = Path(os.environ.get("FABRIC_ROOT") or str(REPO_ROOT / "data" / "map_fabric"))
    samples_dir = Path(os.environ.get("SAMPLES_DIR") or str(REPO_ROOT / "data" / "samples"))
    db_path = Path(os.environ.get("DB_PATH") or str(REPO_ROOT / "data" / "livemap.db"))
    uploads_dir = Path(os.environ.get("UPLOADS_DIR") or str(REPO_ROOT / "data" / "uploads"))
    hot_facilities_path = Path(
        os.environ.get("HOT_FACILITIES_PATH") or str(REPO_ROOT / "data" / "hot_facilities.json")
    )
    return Settings(
        baidu_ak=ak,
        demo_mode=demo_mode,
        cache_dir=cache_dir,
        fabric_root=fabric_root,
        samples_dir=samples_dir,
        db_path=db_path,
        uploads_dir=uploads_dir,
        hot_facilities_path=hot_facilities_path,
    )
