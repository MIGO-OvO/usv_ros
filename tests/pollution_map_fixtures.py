from pathlib import Path


def _position(lat, lng, source="real", lab_mode=False):
    return {
        "wgs84": {"lat": lat, "lng": lng, "alt": 0.0},
        "gcj02": {"lat": lat, "lng": lng, "alt": 0.0},
        "position_source": source,
        "lab_mode": lab_mode,
    }


def create_pollution_map_fixture(module, tmpdir):
    """Create a real mission JSON with route, GPS samples, quality, and COD values."""
    missions_dir = str(Path(tmpdir) / "missions")
    manager = module.MissionDataManager(missions_dir)
    manager.start_mission("pollution-map-fixture")
    mission_id = manager.current_mission_data["mission_id"]
    metric_config = {
        "enabled": True,
        "slope": 1.0,
        "intercept": 0.0,
        "unit": "mg/L",
        "display_name": "COD",
        "pollutant_name": "COD",
        "method_name": "UV254",
        "calibration_id": "fixture-calibration",
        "min_valid": 0.0,
        "max_valid": 1.0,
    }

    route = [
        dict(_position(30.0000, 120.0000), seq=1),
        dict(_position(30.0010, 120.0000), seq=2),
        dict(_position(30.0000, 120.0010), seq=3),
    ]
    manager.set_route_waypoints(route)
    manager.current_mission_data["track_points"] = [
        _position(30.0000, 120.0000),
        _position(30.0005, 120.0004),
        _position(30.0010, 120.0010),
    ]

    for idx, (lat, lng, absorbance) in enumerate([
        (30.0000, 120.0000, 0.2),
        (30.0010, 120.0000, 0.4),
        (30.0000, 120.0010, 0.6),
    ], start=1):
        manager.add_data_point(
            1.0,
            absorbance,
            position=_position(lat, lng),
            waypoint_seq=idx,
            spectrometer_raw={"valid": True, "baseline_set": True},
            pollution_metric=metric_config,
            system_health={"health": {"summary": "fixture"}},
        )

    manager.add_data_point(
        1.0,
        0.8,
        spectrometer_raw={"valid": True, "baseline_set": True},
        pollution_metric=metric_config,
    )
    manager.add_data_point(
        1.0,
        1.2,
        position=_position(30.0020, 120.0000),
        spectrometer_raw={"valid": True, "baseline_set": True},
        pollution_metric=metric_config,
    )
    manager._save_current()

    return {
        "mission_id": mission_id,
        "missions_dir": missions_dir,
        "metric_config": metric_config,
    }
