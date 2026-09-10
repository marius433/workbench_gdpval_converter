"""Occupation and sector resolution.

GDPval pairs each occupation with exactly one sector; sector is therefore
never chosen independently. ``GDPVAL_SECTOR`` is derived from all 220 rows of
``openai/gdpval`` and is the authoritative pairing. Category → occupation
mappings are deliberately crude and extended per bundle via
``occupation_map.json`` (never guessed — unmapped stays UNMAPPED and is
recorded in the manifest).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

UNMAPPED = "UNMAPPED"

# Built-in category -> (occupation, sector) table. Extend per bundle via an
# occupation_map.json in the bundle root or passed explicitly.
DEFAULT_CATEGORY_MAP: dict[str, tuple[str, str]] = {
    "strategy": (
        "Project Management Specialists",
        "Professional, Scientific, and Technical Services",
    ),
    "operational_dd": ("General and Operations Managers", "Retail Trade"),
    "market_sizing": ("Financial and Investment Analysts", "Finance and Insurance"),
}

# Occupation -> sector, derived from all 220 rows of openai/gdpval.
GDPVAL_SECTOR: dict[str, str] = {
    "Accountants and Auditors": "Professional, Scientific, and Technical Services",
    "Buyers and Purchasing Agents": "Manufacturing",
    "Compliance Officers": "Government",
    "Computer and Information Systems Managers": (
        "Professional, Scientific, and Technical Services"
    ),
    "Financial Managers": "Finance and Insurance",
    "Financial and Investment Analysts": "Finance and Insurance",
    "First-Line Supervisors of Retail Sales Workers": "Retail Trade",
    "General and Operations Managers": "Retail Trade",
    "Industrial Engineers": "Manufacturing",
    "Lawyers": "Professional, Scientific, and Technical Services",
    "Project Management Specialists": "Professional, Scientific, and Technical Services",
    "Sales Managers": "Wholesale Trade",
    "Software Developers": "Professional, Scientific, and Technical Services",
}

TASK_ID_RE = re.compile(r"^ME-[A-Z]-\d+-P\d+$")


@dataclass
class OccupationResolver:
    """Resolves (occupation, sector, source) for a task.

    ``occupation_map.json`` may key either a category or a bare task id
    (per-task override); a per-task override wins over the category table.
    Sector always follows the occupation via ``GDPVAL_SECTOR`` when the map
    does not state one explicitly.
    """

    by_category: dict[str, tuple[str, ...]] = field(
        default_factory=lambda: {k: tuple(v) for k, v in DEFAULT_CATEGORY_MAP.items()}
    )
    by_task: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def load_map(self, path: str) -> None:
        """Merge an occupation_map.json. Values may be a string (occupation
        only) or a [occupation, sector] pair; keys may be categories or
        task ids."""
        with open(path) as f:
            data: dict[str, str | list[str]] = json.load(f)
        for key, value in data.items():
            entry = (value,) if isinstance(value, str) else tuple(value)
            if TASK_ID_RE.match(key):
                self.by_task[key] = entry
            else:
                self.by_category[key] = entry

    def load_maps_near(self, bundle_dir: str, extra: str | None = None) -> list[str]:
        """Load the bundle-root occupation_map.json plus an optional explicit
        map. Returns the paths actually loaded (for the manifest)."""
        loaded = []
        candidates = [os.path.join(bundle_dir, "occupation_map.json")]
        if extra:
            candidates.append(extra)
        for p in candidates:
            if os.path.isfile(p):
                self.load_map(p)
                loaded.append(os.path.abspath(p))
        return loaded

    def resolve(self, task_id: str, category: str) -> tuple[str, str, str]:
        """Return (occupation, sector, source). Never guesses: an unknown
        category resolves to UNMAPPED and the caller records it."""
        entry = self.by_task.get(task_id) or self.by_category.get(category)
        if not entry:
            return UNMAPPED, UNMAPPED, "unmapped"
        source = "task-override" if task_id in self.by_task else "category-table"
        occupation = entry[0]
        sector = entry[1] if len(entry) > 1 else GDPVAL_SECTOR.get(occupation, UNMAPPED)
        return occupation, sector, source
