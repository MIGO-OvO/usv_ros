#!/usr/bin/env python3
"""Offline verifier for the Web-first pollutant map workflow."""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import sys
import tempfile
from io import StringIO
from pathlib import Path
from typing import Any


SCRIPT_PATH = Path(__file__).resolve()
REPO_ROOT = SCRIPT_PATH.parents[1]
WORKSPACE_ROOT = REPO_ROOT.parents[1]
TESTS_DIR = REPO_ROOT / "tests"


def _load_fixture_module():
    if str(TESTS_DIR) not in sys.path:
        sys.path.insert(0, str(TESTS_DIR))
    fixture_path = TESTS_DIR / "pollution_map_fixtures.py"
    spec = importlib.util.spec_from_file_location("pollution_map_fixtures_verify", fixture_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load pollution_map_fixtures.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _resolve_evidence_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    text = value.replace("\\", "/")
    if text.startswith(".omo/"):
        return WORKSPACE_ROOT / path
    return Path.cwd() / path


def _json_response(response, name: str) -> dict[str, Any]:
    if response.status_code != 200:
        raise AssertionError(f"{name} returned HTTP {response.status_code}")
    payload = response.get_json()
    if not isinstance(payload, dict):
        raise AssertionError(f"{name} did not return a JSON object")
    if payload.get("success") is False:
        raise AssertionError(f"{name} returned success=false: {payload}")
    return payload


def _check(condition: bool, label: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    if not condition:
        raise AssertionError(label)
    return {"name": label, "ok": True, "details": details or {}}


def _csv_rows(response) -> list[dict[str, str]]:
    if response.status_code != 200:
        raise AssertionError(f"csv returned HTTP {response.status_code}")
    text = response.get_data(as_text=True)
    rows = list(csv.DictReader(StringIO(text)))
    if not rows:
        raise AssertionError("csv export returned no data rows")
    return rows


def _frontend_artifacts() -> dict[str, Any]:
    package_path = REPO_ROOT / "frontend" / "package.json"
    package = json.loads(package_path.read_text(encoding="utf-8"))
    dist_index = REPO_ROOT / "static" / "dist" / "index.html"
    smoke_script = REPO_ROOT / "frontend" / "scripts" / "smoke-map.mjs"
    browser_smoke_script = REPO_ROOT / "frontend" / "scripts" / "smoke-map-browser.mjs"
    assets = sorted((REPO_ROOT / "static" / "dist" / "assets").glob("index-*"))
    return {
        "package_json": str(package_path),
        "scripts": {
            "smoke_map": package.get("scripts", {}).get("smoke:map"),
            "smoke_map_browser": package.get("scripts", {}).get("smoke:map:browser"),
        },
        "dist_index": str(dist_index),
        "dist_index_exists": dist_index.is_file(),
        "asset_count": len(assets),
        "smoke_script_exists": smoke_script.is_file(),
        "browser_smoke_script_exists": browser_smoke_script.is_file(),
    }


def run_mock_verification(size: int, power: float) -> dict[str, Any]:
    import web_config_server

    fixture_module = _load_fixture_module()
    checks: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory(prefix="usv-pollution-verify-") as tmpdir:
        fixture = fixture_module.create_pollution_map_fixture(web_config_server, tmpdir)
        server = web_config_server.WebConfigServer(standalone=True)
        server.data_manager = web_config_server.MissionDataManager(fixture["missions_dir"])
        client = server.app.test_client()
        mission_id = fixture["mission_id"]

        missions_payload = _json_response(client.get("/api/data/missions"), "missions")
        missions = missions_payload.get("data") or []
        mission_ids = {item.get("id") for item in missions if isinstance(item, dict)}
        checks.append(_check(mission_id in mission_ids, "mission list contains mock mission", {"mission_id": mission_id}))

        mission_payload = _json_response(client.get(f"/api/data/mission/{mission_id}"), "mission")
        mission_data = mission_payload.get("data") or {}
        checks.append(_check(len(mission_data.get("data_points") or []) >= 5, "mission has mock samples"))

        csv_response = client.get(f"/api/data/mission/{mission_id}/csv")
        rows = _csv_rows(csv_response)
        checks.append(_check(len(rows) >= 5, "csv export has data rows", {"csv_rows": len(rows)}))

        geo_payload = _json_response(
            client.get(f"/api/data/mission/{mission_id}/geojson?metric=concentration"),
            "geojson",
        )
        geojson = geo_payload.get("data") or {}
        features = geojson.get("features") or []
        samples = [feature for feature in features if (feature.get("properties") or {}).get("layer") == "sample"]
        geo_meta = geojson.get("meta") or {}
        checks.append(_check(len(samples) >= 3, "geojson contains valid sample features", {"sample_features": len(samples)}))
        checks.append(_check(geo_meta.get("pollutant_name") == "COD", "geojson meta pollutant is COD", geo_meta))
        checks.append(_check(geo_meta.get("calibration_id") == "fixture-calibration", "geojson meta calibration is present"))
        checks.append(_check(int(geo_meta.get("valid_surface_point_count") or 0) >= 3, "geojson reports valid surface points"))

        surface_payload = _json_response(
            client.get(f"/api/data/mission/{mission_id}/surface?metric=concentration&size={size}&power={power}"),
            "surface",
        )
        surface = surface_payload.get("data") or {}
        surface_meta = surface.get("meta") or {}
        excluded_reasons = surface_meta.get("excluded_reasons") or surface.get("excluded_reasons") or {}
        checks.append(_check(bool(surface.get("valid")), "surface is valid"))
        checks.append(_check(len(surface.get("grid") or []) == size * size, "surface grid size matches request", {"surface_grid": f"{size}x{size}"}))
        checks.append(_check(surface_meta.get("unit") == "mg/L", "surface unit is mg/L", surface_meta))
        checks.append(_check(int(surface_meta.get("valid_surface_point_count") or surface.get("point_count") or 0) == 3, "surface uses three valid points"))
        checks.append(_check(int(surface_meta.get("excluded_count") or surface.get("excluded_count") or 0) >= 2, "surface records excluded points"))
        checks.append(_check("missing_gps" in excluded_reasons, "surface records missing GPS exclusion", excluded_reasons))
        checks.append(_check("above_max_valid" in excluded_reasons, "surface records range exclusion", excluded_reasons))

        download_geo = client.get(f"/api/data/mission/{mission_id}/geojson?metric=concentration&download=true")
        download_surface = client.get(f"/api/data/mission/{mission_id}/surface?metric=concentration&size={size}&power={power}&download=true")
        checks.append(_check("attachment" in (download_geo.headers.get("Content-Disposition") or ""), "geojson download has attachment header"))
        checks.append(_check("attachment" in (download_surface.headers.get("Content-Disposition") or ""), "surface download has attachment header"))

    frontend = _frontend_artifacts()
    checks.append(_check(frontend["dist_index_exists"], "frontend dist index exists", frontend))
    checks.append(_check(frontend["asset_count"] > 0, "frontend dist assets exist", frontend))
    checks.append(_check(frontend["scripts"]["smoke_map"] == "node scripts/smoke-map.mjs", "static map smoke script declared", frontend["scripts"]))
    checks.append(_check(frontend["scripts"]["smoke_map_browser"] == "node scripts/smoke-map-browser.mjs", "browser map smoke script declared", frontend["scripts"]))
    checks.append(_check(frontend["browser_smoke_script_exists"], "browser smoke script exists", frontend))

    return {
        "ok": True,
        "mode": "mock",
        "hardware_required": False,
        "mission_id": mission_id,
        "metric": "concentration",
        "pollutant_name": "COD",
        "unit": "mg/L",
        "calibration_id": "fixture-calibration",
        "surface_grid": f"{size}x{size}",
        "idw_power": power,
        "checks": checks,
        "frontend": frontend,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify the pollutant map workflow offline.")
    parser.add_argument("--mock", action="store_true", help="create a deterministic mock mission and avoid real hardware")
    parser.add_argument("--evidence", required=True, help="path to write JSON evidence")
    parser.add_argument("--size", type=int, default=50, help="IDW grid size")
    parser.add_argument("--power", type=float, default=2.0, help="IDW power")
    args = parser.parse_args(argv)

    if not args.mock:
        parser.error("only --mock verification is supported; real hardware is intentionally not used")
    if args.size < 3:
        parser.error("--size must be >= 3")

    evidence_path = _resolve_evidence_path(args.evidence)
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    result = run_mock_verification(args.size, args.power)
    evidence_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("pollution workflow offline verify ok")
    print(json.dumps({
        "evidence": str(evidence_path),
        "mission_id": result["mission_id"],
        "checks": len(result["checks"]),
        "hardware_required": result["hardware_required"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
