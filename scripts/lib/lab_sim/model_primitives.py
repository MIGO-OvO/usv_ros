from __future__ import annotations

from dataclasses import dataclass

from scripts.lib.lab_sim.model_parsing import (
    JsonValue,
    ModelParseError,
    mapping_value,
    number_value,
)


@dataclass(frozen=True)
class GeoPoint:
    lat: float
    lng: float
    alt: float | None = None

    @classmethod
    def from_dict(cls, raw: JsonValue, path: str) -> GeoPoint:
        data = mapping_value(raw, path)
        lat = number_value(data.get("lat"), path + ".lat")
        lng = number_value(data.get("lng"), path + ".lng")
        if not -90.0 <= lat <= 90.0 or not -180.0 <= lng <= 180.0:
            raise ModelParseError("coordinate_range", path, "latitude or longitude out of range")
        alt_raw = data.get("alt")
        alt = None if alt_raw is None else number_value(alt_raw, path + ".alt")
        return cls(lat=lat, lng=lng, alt=alt)

    def to_dict(self) -> dict[str, JsonValue]:
        data: dict[str, JsonValue] = {"lat": self.lat, "lng": self.lng}
        if self.alt is not None:
            data["alt"] = self.alt
        return data


@dataclass(frozen=True)
class CoordinatePairRef:
    wgs84: GeoPoint
    gcj02: GeoPoint

    @classmethod
    def from_dict(cls, raw: JsonValue, path: str) -> CoordinatePairRef:
        data = mapping_value(raw, path)
        if "lat" in data or "lng" in data:
            raise ModelParseError("ambiguous_coordinate", path, "bare lat/lng has no CRS")
        if "wgs84" not in data or "gcj02" not in data:
            raise ModelParseError("missing_crs", path, "both wgs84 and gcj02 are required")
        return cls(
            wgs84=GeoPoint.from_dict(data["wgs84"], path + ".wgs84"),
            gcj02=GeoPoint.from_dict(data["gcj02"], path + ".gcj02"),
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return {"wgs84": self.wgs84.to_dict(), "gcj02": self.gcj02.to_dict()}
