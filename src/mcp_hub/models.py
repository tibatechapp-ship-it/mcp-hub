from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Server:
    name: str
    publisher: str = ""
    description: str = ""
    source: str = ""
    registry_id: str = ""
    repository_url: str = ""
    endpoint_url: str = ""
    package_name: str = ""
    categories: str = ""
    raw_name: str = ""
    popularity: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("extra", None)
        # normalize string fields
        for k in (
            "name",
            "publisher",
            "description",
            "source",
            "registry_id",
            "repository_url",
            "endpoint_url",
            "package_name",
            "categories",
            "raw_name",
        ):
            data[k] = (data.get(k) or "").strip()
        data["popularity"] = int(self.popularity or 0)
        return data
