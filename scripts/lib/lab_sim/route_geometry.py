from __future__ import annotations

import heapq
import math
from collections.abc import Sequence
from typing import Final

EPSILON_M: Final = 0.01
Point = tuple


def cross_product(a: Point, b: Point, c: Point) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def segments_intersect(a: Point, b: Point, c: Point, d: Point) -> bool:
    values = (cross_product(a, b, c), cross_product(a, b, d), cross_product(c, d, a), cross_product(c, d, b))
    if values[0] * values[1] < 0.0 and values[2] * values[3] < 0.0:
        return True
    return (
        _on_segment(c, a, b)
        or _on_segment(d, a, b)
        or _on_segment(a, c, d)
        or _on_segment(b, c, d)
    )


def rotate(point: Point, heading_rad: float) -> Point:
    along = point[0] * math.sin(heading_rad) + point[1] * math.cos(heading_rad)
    across = -point[0] * math.cos(heading_rad) + point[1] * math.sin(heading_rad)
    return along, across


def unrotate(point: Point, heading_rad: float) -> Point:
    return (
        point[0] * math.sin(heading_rad) - point[1] * math.cos(heading_rad),
        point[0] * math.cos(heading_rad) + point[1] * math.sin(heading_rad),
    )


def scan_runs(polygon: Sequence[Point], spacing: float, clearance: float) -> list[tuple[Point, Point]]:
    min_v, max_v = min(point[1] for point in polygon), max(point[1] for point in polygon)
    usable = max_v - min_v - 2.0 * clearance
    if usable < -1e-7:
        return []
    row_count = max(1, math.floor(max(0.0, usable) / spacing) + 1)
    center = (min_v + max_v) / 2.0
    rows = tuple(center + (index - (row_count - 1) / 2.0) * spacing for index in range(row_count))
    runs: list[tuple[Point, Point]] = []
    for row in rows:
        runs.extend(_runs_for_row(polygon, row, clearance))
    return runs


def connection_nodes(polygon: Sequence[Point], clearance: float) -> tuple[Point, ...]:
    radius = max(0.08, clearance * 1.6)
    candidates = tuple(
        (
            vertex[0] + radius * math.cos(index * math.pi / 8.0),
            vertex[1] + radius * math.sin(index * math.pi / 8.0),
        )
        for vertex in polygon
        for index in range(16)
    )
    return tuple(point for point in candidates if _clear(point, polygon, clearance))


def connect_route_segment(
    start: Point,
    end: Point,
    polygon: Sequence[Point],
    clearance: float,
    nodes: Sequence[Point],
) -> list[Point] | None:
    if _segment_clear(start, end, polygon, clearance):
        return [end]
    graph = (start, end, *nodes)
    distances = [math.inf] * len(graph)
    previous = [-1] * len(graph)
    distances[0] = 0.0
    queue = [(0.0, 0)]
    while queue:
        distance, index = heapq.heappop(queue)
        if distance != distances[index]:
            continue
        if index == 1:
            break
        _relax_edges(index, graph, polygon, clearance, distances, previous, queue)
    if not math.isfinite(distances[1]):
        return None
    path: list[Point] = []
    cursor = 1
    while cursor != 0:
        path.append(graph[cursor])
        cursor = previous[cursor]
    path.reverse()
    return path


def _on_segment(point: Point, start: Point, end: Point, tolerance: float = 1e-8) -> bool:
    return (
        abs(cross_product(start, end, point)) <= tolerance
        and min(start[0], end[0]) - tolerance <= point[0] <= max(start[0], end[0]) + tolerance
        and min(start[1], end[1]) - tolerance <= point[1] <= max(start[1], end[1]) + tolerance
    )


def _distance_to_segment(point: Point, start: Point, end: Point) -> float:
    dx, dy = end[0] - start[0], end[1] - start[1]
    length_sq = dx * dx + dy * dy
    ratio = 0.0
    if length_sq > 0.0:
        ratio = ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / length_sq
    ratio = max(0.0, min(1.0, ratio))
    return math.hypot(
        point[0] - (start[0] + ratio * dx),
        point[1] - (start[1] + ratio * dy),
    )


def _segment_distance(a: Point, b: Point, c: Point, d: Point) -> float:
    if segments_intersect(a, b, c, d):
        return 0.0
    return min(
        _distance_to_segment(a, c, d),
        _distance_to_segment(b, c, d),
        _distance_to_segment(c, a, b),
        _distance_to_segment(d, a, b),
    )


def _inside(point: Point, polygon: Sequence[Point]) -> bool:
    inside = False
    previous = polygon[-1]
    for current in polygon:
        if _on_segment(point, previous, current):
            return True
        if (current[1] > point[1]) != (previous[1] > point[1]):
            crossing_x = (previous[0] - current[0]) * (point[1] - current[1]) / (previous[1] - current[1]) + current[0]
            if point[0] <= crossing_x:
                inside = not inside
        previous = current
    return inside


def _clear(point: Point, polygon: Sequence[Point], clearance: float) -> bool:
    return _inside(point, polygon) and min(
        _distance_to_segment(point, polygon[index - 1], polygon[index])
        for index in range(len(polygon))
    ) >= clearance - 1e-7


def _segment_clear(start: Point, end: Point, polygon: Sequence[Point], clearance: float) -> bool:
    midpoint = ((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0)
    return _inside(midpoint, polygon) and all(
        _segment_distance(start, end, polygon[index - 1], polygon[index]) >= clearance - 1e-7
        for index in range(len(polygon))
    )


def _runs_for_row(polygon: Sequence[Point], row: float, clearance: float) -> list[tuple[Point, Point]]:
    crossings = _row_crossings(polygon, row)
    forbidden = _merge(
        tuple(
            interval
            for index in range(len(polygon))
            for interval in _capsule_intervals(polygon[index - 1], polygon[index], row, clearance)
        )
    )
    runs: list[tuple[Point, Point]] = []
    for lower, upper in zip(crossings[::2], crossings[1::2]):
        allowed = [(lower, upper)]
        for blocked_start, blocked_end in forbidden:
            allowed = [
                piece
                for start, end in allowed
                for piece in ((start, min(end, blocked_start)), (max(start, blocked_end), end))
                if piece[1] - piece[0] > 2.0 * EPSILON_M
            ]
        runs.extend(((start + EPSILON_M, row), (end - EPSILON_M, row)) for start, end in allowed)
    return runs


def _row_crossings(polygon: Sequence[Point], row: float) -> list[float]:
    crossings: list[float] = []
    for index, current in enumerate(polygon):
        previous = polygon[index - 1]
        if (current[1] > row) != (previous[1] > row):
            crossings.append(
                current[0] + (row - current[1]) * (previous[0] - current[0]) / (previous[1] - current[1])
            )
    return sorted(crossings)


def _merge(intervals: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    merged: list[tuple[float, float]] = []
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if merged and start <= merged[-1][1] + 1e-9:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _capsule_intervals(start: Point, end: Point, row: float, radius: float) -> list[tuple[float, float]]:
    intervals = _endpoint_intervals(start, end, row, radius)
    dx, dy = end[0] - start[0], end[1] - start[1]
    length_sq = dx * dx + dy * dy
    length = math.sqrt(length_sq)
    projection = _projection_interval(start, row, dx, dy, length_sq)
    if projection is None:
        return intervals
    perpendicular = _perpendicular_interval(start, row, radius, dx, dy, length)
    if perpendicular is None:
        return intervals
    lower, upper = max(projection[0], perpendicular[0]), min(projection[1], perpendicular[1])
    if lower < upper:
        intervals.append((lower, upper))
    return intervals


def _endpoint_intervals(start: Point, end: Point, row: float, radius: float) -> list[tuple[float, float]]:
    intervals: list[tuple[float, float]] = []
    for point in (start, end):
        vertical = row - point[1]
        if abs(vertical) <= radius:
            half = math.sqrt(max(0.0, radius * radius - vertical * vertical))
            intervals.append((point[0] - half, point[0] + half))
    return intervals


def _projection_interval(start: Point, row: float, dx: float, dy: float, length_sq: float) -> tuple[float, float] | None:
    if abs(dx) > 1e-12:
        values = (
            start[0] - (row - start[1]) * dy / dx,
            start[0] + (length_sq - (row - start[1]) * dy) / dx,
        )
        return min(values), max(values)
    if 0.0 <= (row - start[1]) * dy <= length_sq:
        return -math.inf, math.inf
    return None


def _perpendicular_interval(start: Point, row: float, radius: float, dx: float, dy: float, length: float) -> tuple[float, float] | None:
    perpendicular_value = dx * (row - start[1])
    if abs(dy) > 1e-12:
        values = (
            start[0] + (perpendicular_value - radius * length) / dy,
            start[0] + (perpendicular_value + radius * length) / dy,
        )
        return min(values), max(values)
    if abs(perpendicular_value) <= radius * length:
        return -math.inf, math.inf
    return None


def _relax_edges(
    index: int,
    graph: tuple[Point, ...],
    polygon: Sequence[Point],
    clearance: float,
    distances: list[float],
    previous: list[int],
    queue: list[tuple[float, int]],
) -> None:
    for other in range(1, len(graph)):
        if other == index or not _segment_clear(graph[index], graph[other], polygon, clearance):
            continue
        candidate = distances[index] + math.dist(graph[index], graph[other])
        if candidate + 1e-9 < distances[other]:
            distances[other], previous[other] = candidate, index
            heapq.heappush(queue, (candidate, other))
