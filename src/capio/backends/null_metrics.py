"""Null metrics backend: records metric dicts in memory for inspection/tests (RFC-019 §3.3)."""

from __future__ import annotations

from typing import Any, Dict, List


class NullMetricsBackend:
    """Accumulates metric records; useful as the default in test/dev profiles."""

    def __init__(self) -> None:
        self.records: List[Dict[str, Any]] = []

    def record(self, metric: Dict[str, Any]) -> None:
        self.records.append(dict(metric))

    def clear(self) -> None:
        self.records.clear()

    def by_name(self, name: str) -> List[Dict[str, Any]]:
        return [r for r in self.records if r.get("name") == name]

    def counters(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for record in self.records:
            name = record.get("name", "")
            if record.get("kind") == "counter":
                counts[name] = counts.get(name, 0) + 1
        return counts

    def histograms(self) -> Dict[str, List[float]]:
        hists: Dict[str, List[float]] = {}
        for record in self.records:
            if record.get("kind") == "histogram":
                hists.setdefault(record.get("name", ""), []).append(float(record.get("value", 0)))
        return hists
