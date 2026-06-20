from __future__ import annotations

from dataclasses import dataclass

from scripts.lib.lab_sim.model_parsing import (
    JsonObject,
    JsonValue,
    ModelParseError,
    boolean_value,
    integer_value,
    mapping_value,
    number_value,
    optional_integer_value,
    schema_version_value,
    sequence_value,
    string_value,
)
from scripts.lib.lab_sim.model_primitives import CoordinatePairRef


@dataclass(frozen=True)
class DropletResult:
    droplet_index: int
    offset_ms: int
    voltage: float
    absorbance: float
    truth_concentration: float
    estimated_concentration: float
    valid: bool
    saturated: bool
    noise_flags: tuple[str, ...]

    @classmethod
    def from_dict(cls, raw: JsonValue, path: str) -> DropletResult:
        data = mapping_value(raw, path)
        flags = tuple(
            string_value(value, "{}.noise_flags[{}]".format(path, index))
            for index, value in enumerate(sequence_value(data.get("noise_flags"), path + ".noise_flags"))
        )
        return cls(
            integer_value(data.get("droplet_index"), path + ".droplet_index"),
            integer_value(data.get("offset_ms"), path + ".offset_ms"),
            number_value(data.get("voltage"), path + ".voltage"),
            number_value(data.get("absorbance"), path + ".absorbance"),
            number_value(data.get("truth_concentration"), path + ".truth_concentration"),
            number_value(data.get("estimated_concentration"), path + ".estimated_concentration"),
            boolean_value(data.get("valid"), path + ".valid"),
            boolean_value(data.get("saturated"), path + ".saturated"),
            flags,
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "droplet_index": self.droplet_index, "offset_ms": self.offset_ms,
            "voltage": self.voltage, "absorbance": self.absorbance,
            "truth_concentration": self.truth_concentration,
            "estimated_concentration": self.estimated_concentration,
            "valid": self.valid, "saturated": self.saturated,
            "noise_flags": list(self.noise_flags),
        }


@dataclass(frozen=True)
class SamplingEvent:
    schema_version: int
    event_id: str
    mode: str
    route_id: str
    waypoint_index: int | None
    segment_index: int | None
    position: CoordinatePairRef
    analyte_id: str
    droplets: tuple[DropletResult, ...]
    mean: float
    median: float
    standard_deviation: float
    valid_count: int
    quality_flags: tuple[str, ...]
    config_droplet_count: int

    @property
    def valid(self) -> bool:
        return "insufficient_valid_droplets" not in self.quality_flags

    @classmethod
    def from_dict(cls, raw: JsonObject) -> SamplingEvent:
        data = mapping_value(raw, "$")
        route_ref = mapping_value(data.get("route_ref"), "$.route_ref")
        mode = string_value(data.get("mode"), "$.mode")
        if mode not in ("waypoint", "survey"):
            raise ModelParseError("sampling_mode", "$.mode", mode)
        snapshot = mapping_value(data.get("config_snapshot"), "$.config_snapshot")
        schema_version_value(snapshot.get("schema_version"), "$.config_snapshot.schema_version")
        count = integer_value(snapshot.get("droplet_count"), "$.config_snapshot.droplet_count")
        if not 3 <= count <= 64:
            raise ModelParseError("droplet_count_range", "$.config_snapshot.droplet_count", str(count))
        droplets = tuple(
            DropletResult.from_dict(value, "$.droplets[{}]".format(index))
            for index, value in enumerate(sequence_value(data.get("droplets"), "$.droplets"))
        )
        if len(droplets) != count:
            raise ModelParseError("droplet_count_mismatch", "$.droplets", str(len(droplets)))
        return cls(
            schema_version_value(data.get("schema_version"), "$.schema_version"),
            string_value(data.get("event_id"), "$.event_id"), mode,
            string_value(route_ref.get("route_id"), "$.route_ref.route_id"),
            optional_integer_value(route_ref.get("waypoint_index"), "$.route_ref.waypoint_index"),
            optional_integer_value(route_ref.get("segment_index"), "$.route_ref.segment_index"),
            CoordinatePairRef.from_dict(data.get("position"), "$.position"),
            string_value(data.get("analyte_id"), "$.analyte_id"),
            droplets,
            number_value(data.get("mean"), "$.mean"),
            number_value(data.get("median"), "$.median"),
            number_value(data.get("standard_deviation"), "$.standard_deviation"),
            integer_value(data.get("valid_count"), "$.valid_count"),
            tuple(
                string_value(value, "$.quality_flags[{}]".format(index))
                for index, value in enumerate(sequence_value(data.get("quality_flags"), "$.quality_flags"))
            ),
            count,
        )

    def to_dict(self) -> dict[str, JsonValue]:
        route_ref: dict[str, JsonValue] = {"route_id": self.route_id}
        if self.waypoint_index is not None:
            route_ref["waypoint_index"] = self.waypoint_index
        if self.segment_index is not None:
            route_ref["segment_index"] = self.segment_index
        return {
            "schema_version": self.schema_version, "event_id": self.event_id, "mode": self.mode,
            "route_ref": route_ref, "position": self.position.to_dict(),
            "analyte_id": self.analyte_id,
            "droplets": [value.to_dict() for value in self.droplets],
            "mean": self.mean, "median": self.median,
            "standard_deviation": self.standard_deviation, "valid_count": self.valid_count,
            "quality_flags": list(self.quality_flags),
            "config_snapshot": {"schema_version": 2, "droplet_count": self.config_droplet_count},
        }
