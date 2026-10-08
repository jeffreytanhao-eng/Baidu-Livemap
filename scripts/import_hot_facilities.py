#!/usr/bin/env python3
"""import_hot_facilities.py — 校验/维护 data/hot_facilities.json（重点校/三甲名单）。

名单为手工整理的静态文件（市教委示范校、卫健委三甲名录口径），随代码提交。
本脚本职责：
1. 结构校验：type 必须是 key_middle_school / key_primary / sanjia_hospital；
   name 非空；aliases 为字符串数组；
2. 去重：type + 规范化名（app.db.normalize_name）重复的条目合并 aliases；
3. 可选注入坐标：--with-coords 时用百度 geocoding（恒 bd09）按 name 补
   lng/lat 并转 WGS84 回写（配额紧张时慎用；打标匹配只依赖 name/aliases，
   坐标仅备用）。

用法：
    python scripts/import_hot_facilities.py            # 校验 + 去重（回写）
    python scripts/import_hot_facilities.py --with-coords
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))
sys.path.insert(0, str(REPO_ROOT / "packages" / "geo"))

from app.db import normalize_name  # noqa: E402

VALID_TYPES = {"key_middle_school", "key_primary", "sanjia_hospital"}
DATA_PATH = REPO_ROOT / "data" / "hot_facilities.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="校验/去重 data/hot_facilities.json")
    parser.add_argument("--with-coords", action="store_true", help="用百度 geocoding 补坐标（耗配额）")
    args = parser.parse_args()

    if not DATA_PATH.is_file():
        print(f"名单文件不存在：{DATA_PATH}")
        return 1
    entries = json.loads(DATA_PATH.read_text(encoding="utf-8"))

    # 结构校验 + 按 (type, norm) 去重合并
    merged: dict[tuple[str, str], dict] = {}
    errors: list[str] = []
    for i, e in enumerate(entries):
        t = str(e.get("type") or "")
        name = str(e.get("name") or "").strip()
        if t not in VALID_TYPES:
            errors.append(f"[{i}] 非法 type: {t}")
            continue
        if not name:
            errors.append(f"[{i}] name 为空")
            continue
        aliases = [str(a).strip() for a in (e.get("aliases") or []) if str(a).strip()]
        key = (t, normalize_name(name))
        if key in merged:
            merged[key]["aliases"] = sorted(set(merged[key]["aliases"] + aliases))
        else:
            merged[key] = {"type": t, "name": name, "aliases": sorted(set(aliases))}
    if errors:
        print("结构错误：")
        for e in errors:
            print(" ", e)
        return 1

    out = list(merged.values())

    if args.with_coords:
        import os

        import requests
        from dotenv import load_dotenv

        from geo import bd09ll_to_wgs84

        load_dotenv(REPO_ROOT / ".env")
        ak = (os.environ.get("BAIDU_SERVER_AK") or "").strip()
        if not ak:
            print("--with-coords 需要 BAIDU_SERVER_AK")
            return 1
        for e in out:
            if e.get("lng") is not None:
                continue
            try:
                resp = requests.get(
                    "https://api.map.baidu.com/geocoding/v3/",
                    params={"address": f"北京市{e['name']}", "output": "json", "ak": ak},
                    timeout=15,
                )
                loc = (resp.json().get("result") or {}).get("location") or {}
            except (requests.RequestException, ValueError) as exc:
                print(f"  ? {e['name']}：geocode 失败 {exc}")
                continue
            if "lng" not in loc:
                print(f"  ? {e['name']}：无结果")
                continue
            lng, lat = bd09ll_to_wgs84(float(loc["lng"]), float(loc["lat"]))
            e["lng"], e["lat"] = round(lng, 6), round(lat, 6)
            print(f"  + {e['name']}：({lng:.6f},{lat:.6f})")

    DATA_PATH.write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    by_type: dict[str, int] = {}
    for e in out:
        by_type[e["type"]] = by_type.get(e["type"], 0) + 1
    print(f"OK：共 {len(out)} 条（" + "，".join(f"{k}={v}" for k, v in sorted(by_type.items())) + "）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
