"""checkup 契约：参数校验、境外拒绝、响应字段齐全、白名单标记。"""

from __future__ import annotations

import re

FULL_KEYS = {
    "meta",
    "fastLayer",
    "layers",
    "enclaves",
    "pois",
    "coverage",
    "sectorGaps",
    "blindSpots",
    "blindSummary",
    "diagnosis",
    "straightCircle",
}

META_KEYS = {
    "minutes",
    "engine",
    "demoMode",
    "degraded",
    "cityWhitelist",
    "methodOnly",
    "center",
    "elapsedMs",
    "isochroneArea",
    "circleArea",
    "areaRatio",
}

DIRECTIONS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def test_minutes_out_of_range(client, post_checkup, default_center_bd):
    assert post_checkup(client, default_center_bd, 4).status_code == 400
    assert post_checkup(client, default_center_bd, 31).status_code == 400


def test_minutes_non_integer(client, post_checkup, default_center_bd):
    assert post_checkup(client, default_center_bd, 15.5).status_code == 400
    assert post_checkup(client, default_center_bd, "abc").status_code == 400


def test_overseas_rejected(client, post_checkup):
    # 东京
    r = post_checkup(client, (139.767, 35.681), 15, coord_type="wgs84")
    assert r.status_code == 400
    assert "国内" in r.json()["detail"]
    # 本初子午线
    assert post_checkup(client, (0.0, 51.5), 15, coord_type="wgs84").status_code == 400


def test_full_contract_fields(client, post_checkup, sample_center_bd):
    r = post_checkup(client, sample_center_bd, 15)
    assert r.status_code == 200
    body = r.json()
    assert FULL_KEYS <= set(body)

    meta = body["meta"]
    assert META_KEYS <= set(meta)
    assert meta["minutes"] == 15
    assert meta["circleArea"] > 0
    assert 0.0 <= meta["areaRatio"] <= 1.0
    assert meta["isochroneArea"] > 0
    assert isinstance(meta["elapsedMs"], int)

    assert body["fastLayer"]["type"] == "Feature"
    assert body["fastLayer"]["geometry"]["type"] in ("Polygon", "MultiPolygon")

    # 分层：15 -> [5, 10, 15]
    assert [layer["minutes"] for layer in body["layers"]] == [5, 10, 15]
    for layer in body["layers"]:
        assert layer["polygon"]["type"] == "Feature"
        assert layer["areaM2"] > 0

    for enc in body["enclaves"]:
        assert enc["type"] == "Feature"

    assert len(body["pois"]) > 0
    for poi in body["pois"]:
        assert {"id", "name", "category", "lng", "lat", "walkMinutes", "within"} <= set(poi)

    coverage = body["coverage"]
    assert 0.0 <= coverage["score"] <= 100.0
    assert len(coverage["categories"]) == 7
    for cat in coverage["categories"]:
        assert {"id", "name", "count", "nearestMinutes", "passed"} <= set(cat)
    assert 0 <= coverage["anchor3Passed"] <= 3

    assert [g["direction"] for g in body["sectorGaps"]] == DIRECTIONS
    for gap in body["sectorGaps"]:
        assert {"direction", "isochroneArea", "circleArea", "gapRatio"} <= set(gap)
        assert 0.0 <= gap["gapRatio"] <= 1.0

    assert body["blindSpots"]["type"] == "FeatureCollection"
    assert {"conveniencePct", "healthPct", "educationPct"} <= set(body["blindSummary"])

    assert 3 <= len(body["diagnosis"]) <= 6
    sector_sentences = [d for d in body["diagnosis"] if d.startswith("sector:")]
    assert sector_sentences, "应至少有一条扇区诊断句"
    assert re.match(r"^sector:(N|NE|E|SE|S|SW|W|NW)\s", sector_sentences[0])

    assert body["straightCircle"]["radiusM"] == 80 * 15


def test_whitelist_flags(client, post_checkup, sample_center_bd):
    # 白名单内（劲松）
    body = post_checkup(client, sample_center_bd, 15).json()
    assert body["meta"]["cityWhitelist"] is True
    assert body["meta"]["methodOnly"] is False

    # 白名单内但无预置底图（上海）→ degraded
    body = post_checkup(client, (121.47, 31.23), 12, coord_type="wgs84").json()
    assert body["meta"]["cityWhitelist"] is True
    assert body["meta"]["methodOnly"] is False
    assert body["meta"]["degraded"] is True

    # 白名单外（石家庄）→ methodOnly
    body = post_checkup(client, (114.51, 38.04), 12, coord_type="wgs84").json()
    assert body["meta"]["cityWhitelist"] is False
    assert body["meta"]["methodOnly"] is True


def test_fabric_endpoint(client, default_center_bd):
    r = client.get(
        f"/api/v1/fabric?lng={default_center_bd[0]}&lat={default_center_bd[1]}&radiusM=1500"
    )
    assert r.status_code == 200
    body = r.json()
    assert {"roads", "buildings", "water", "barriers"} <= set(body)
    for key in ("roads", "buildings", "water", "barriers"):
        assert body[key]["type"] == "FeatureCollection"
    assert len(body["roads"]["features"]) > 0
    # 真实采样 fabric：buildings/water/barriers 允许为空，但须为合法 FeatureCollection（上面已断言）
    # 输出坐标应为 BD09LL（与默认 pin 同量级）
    lng = body["roads"]["features"][0]["geometry"]["coordinates"][0][0]
    assert 116.3 < lng < 116.6


def test_misc_endpoints(client, sample_center_bd):
    r = client.get("/health")
    assert r.status_code == 200
    health = r.json()
    assert health["akConfigured"] is False
    assert health["demoMode"] is True
    assert health["version"]
    assert health["graph"]["nodes"] > 0
    assert health["graph"]["edges"] > 0

    samples = client.get("/api/v1/samples").json()
    assert len(samples) == 5
    ids = {s["id"] for s in samples}
    assert ids == {"jinsong_15", "jinsong_5", "jinsong_20", "zhongguancun_15", "nanyuan_15"}
    for s in samples:
        assert {"id", "name", "center", "minutes"} <= set(s)

    r = client.post("/api/v1/geocode", json={"query": "劲松"})
    assert r.status_code == 200
    geo = r.json()
    assert 116.4 < geo["lng"] < 116.5 and 39.8 < geo["lat"] < 39.9
    assert "劲松" in geo["address"]
    assert client.post("/api/v1/geocode", json={"query": "不存在的地名xyz"}).status_code == 404

    r = client.post("/api/v1/reverse", json={"lng": sample_center_bd[0], "lat": sample_center_bd[1]})
    assert r.status_code == 200
    assert "劲松" in r.json()["address"]
