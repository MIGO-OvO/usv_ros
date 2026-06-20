from __future__ import annotations

from dataclasses import dataclass

from scripts.lib.lab_sim.model_parsing import (
    JsonObject,
    JsonValue,
    ModelParseError,
    integer_value,
    mapping_value,
    number_value,
    schema_version_value,
    sequence_value,
    string_value,
)
from scripts.lib.lab_sim.model_primitives import CoordinatePairRef


@dataclass(frozen=True)
class Analyte:
    analyte_id: str
    name: str
    unit: str

    @classmethod
    def from_dict(cls, raw: JsonValue, path: str) -> Analyte:
        data = mapping_value(raw, path)
        return cls(
            analyte_id=string_value(data.get("analyte_id"), path + ".analyte_id"),
            name=string_value(data.get("name"), path + ".name"),
            unit=string_value(data.get("unit"), path + ".unit"),
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return {"analyte_id": self.analyte_id, "name": self.name, "unit": self.unit}


@dataclass(frozen=True)
class PollutionSource:
    source_id: str
    position: CoordinatePairRef
    concentrations: tuple[tuple[str, float], ...]

    @classmethod
    def from_dict(cls, raw: JsonValue, path: str) -> PollutionSource:
        data = mapping_value(raw, path)
        values = mapping_value(data.get("concentrations"), path + ".concentrations")
        concentrations = tuple(
            (key, number_value(value, path + ".concentrations." + key))
            for key, value in sorted(values.items())
        )
        return cls(
            source_id=string_value(data.get("source_id"), path + ".source_id"),
            position=CoordinatePairRef.from_dict(data.get("position"), path + ".position"),
            concentrations=concentrations,
        )

    def to_dict(self) -> dict[str, JsonValue]:
        values: dict[str, JsonValue] = {key: value for key, value in self.concentrations}
        return {
            "source_id": self.source_id,
            "position": self.position.to_dict(),
            "concentrations": values,
        }


@dataclass(frozen=True)
class RouteSnapshot:
    route_id: str
    kind: str
    waypoints: tuple[CoordinatePairRef, ...]

    @classmethod
    def from_dict(cls, raw: JsonValue, path: str) -> RouteSnapshot:
        data = mapping_value(raw, path)
        kind = string_value(data.get("kind"), path + ".kind")
        if kind not in ("manual_route", "auto_scan"):
            raise ModelParseError("route_kind", path + ".kind", kind)
        waypoints = tuple(
            CoordinatePairRef.from_dict(value, "{}.waypoints[{}]".format(path, index))
            for index, value in enumerate(sequence_value(data.get("waypoints"), path + ".waypoints"))
        )
        return cls(string_value(data.get("route_id"), path + ".route_id"), kind, waypoints)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "route_id": self.route_id,
            "kind": self.kind,
            "waypoints": [point.to_dict() for point in self.waypoints],
        }


@dataclass(frozen=True)
class WaterSnapshot:
    snapshot_id: str
    polygon: tuple[CoordinatePairRef, ...]

    @classmethod
    def from_dict(cls, raw: JsonValue, path: str) -> WaterSnapshot:
        data = mapping_value(raw, path)
        polygon = tuple(
            CoordinatePairRef.from_dict(value, "{}.polygon[{}]".format(path, index))
            for index, value in enumerate(sequence_value(data.get("polygon"), path + ".polygon"))
        )
        if len(polygon) < 3:
            raise ModelParseError("water_polygon", path + ".polygon", "at least three points required")
        return cls(string_value(data.get("snapshot_id"), path + ".snapshot_id"), polygon)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "snapshot_id": self.snapshot_id,
            "polygon": [point.to_dict() for point in self.polygon],
        }


@dataclass(frozen=True)
class LabConfigV2:
    schema_version: int
    coordinate_schema_version: int
    droplet_count: int
    analytes: tuple[Analyte, ...]
    sources: tuple[PollutionSource, ...]
    route: RouteSnapshot
    water: WaterSnapshot

    @classmethod
    def from_dict(cls, raw: JsonObject) -> LabConfigV2:
        data = mapping_value(raw, "$")
        droplet_count = integer_value(data.get("droplet_count", 12), "$.droplet_count")
        if not 3 <= droplet_count <= 64:
            raise ModelParseError("droplet_count_range", "$.droplet_count", str(droplet_count))
        analytes = tuple(
            Analyte.from_dict(value, "$.analytes[{}]".format(index))
            for index, value in enumerate(sequence_value(data.get("analytes"), "$.analytes"))
        )
        if not analytes:
            raise ModelParseError("analytes_empty", "$.analytes", "at least one analyte required")
        sources = tuple(
            PollutionSource.from_dict(value, "$.sources[{}]".format(index))
            for index, value in enumerate(sequence_value(data.get("sources"), "$.sources"))
        )
        return cls(
            schema_version_value(data.get("schema_version"), "$.schema_version"),
            schema_version_value(data.get("coordinate_schema_version"), "$.coordinate_schema_version"),
            droplet_count,
            analytes,
            sources,
            RouteSnapshot.from_dict(data.get("route"), "$.route"),
            WaterSnapshot.from_dict(data.get("water"), "$.water"),
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "coordinate_schema_version": self.coordinate_schema_version,
            "droplet_count": self.droplet_count,
            "analytes": [value.to_dict() for value in self.analytes],
            "sources": [value.to_dict() for value in self.sources],
            "route": self.route.to_dict(),
            "water": self.water.to_dict(),
        }
