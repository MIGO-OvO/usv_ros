"""Bounded, single-pass min/max envelopes in source order (no timestamp assumptions)."""

import math


class MinMaxSeries:
    def __init__(self, max_points=2000, fields=("voltage",)):
        self.fields = fields
        self.max_points = max(2 + 2 * len(fields), int(max_points))
        self.bucket_limit = max(1, (self.max_points - 2) // (2 * len(fields)))
        self.width = 1
        self.buckets = {}
        self.count = 0
        self.first = self.last = None
        self.small = []

    def _include(self, bucket, entry):
        for field in self.fields:
            try:
                value = float(entry[1].get(field))
            except (TypeError, ValueError):
                continue
            if not math.isfinite(value):
                continue
            low, high = bucket.get(field, ((value, entry), (value, entry)))
            if value < low[0]:
                low = (value, entry)
            if value > high[0]:
                high = (value, entry)
            bucket[field] = (low, high)

    def add(self, frame):
        entry = (self.count, frame)
        self.first = self.first or entry
        self.last = entry
        self.count += 1
        if self.small is not None:
            self.small.append(frame)
            if len(self.small) > self.max_points:
                self.small = None
        # Double bucket width instead of rereading the stream to learn its length.
        while entry[0] // self.width >= self.bucket_limit:
            self.width *= 2
            merged = {}
            for index, bucket in self.buckets.items():
                target = merged.setdefault(index // 2, {})
                for low, high in bucket.values():
                    self._include(target, low[1])
                    self._include(target, high[1])
            self.buckets = merged
        self._include(self.buckets.setdefault(entry[0] // self.width, {}), entry)

    def samples(self):
        if self.small is not None:
            return self.small
        selected = {entry[0]: entry[1] for entry in (self.first, self.last)}
        for bucket in self.buckets.values():
            for low, high in bucket.values():
                for _, entry in (low, high):
                    selected[entry[0]] = entry[1]
        return [selected[index] for index in sorted(selected)]
