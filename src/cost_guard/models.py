from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class Finding:
    """One wasted (or over-priced) resource and what it costs per month."""

    check: str
    region: str
    resource_id: str
    monthly_cost: float
    summary: str
    action: str
    tags: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["monthly_cost"] = round(self.monthly_cost, 2)
        return data


def tags_to_dict(tags: list[dict] | None) -> dict[str, str]:
    return {t["Key"]: t.get("Value", "") for t in (tags or [])}


def display_name(tags: dict[str, str]) -> str:
    return tags.get("Name", "")
