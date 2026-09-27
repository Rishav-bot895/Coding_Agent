"""Clean sample: JSON parsing, transformation, and validation."""

import json
from typing import Any


def transform_user_record(raw_json: str) -> dict[str, Any]:
    """Parse JSON and normalize user keys."""
    data = json.loads(raw_json)
    if "user_id" not in data:
        raise ValueError("Missing user_id")
    return {
        "id": int(data["user_id"]),
        "name": str(data.get("name", "")).strip().title(),
        "active": bool(data.get("active", False)),
    }


def main() -> None:
    raw = '{"user_id": "101", "name": "alice smith", "active": true}'
    transformed = transform_user_record(raw)
    assert transformed["id"] == 101
    assert transformed["name"] == "Alice Smith"
    assert transformed["active"] is True


if __name__ == "__main__":
    main()

