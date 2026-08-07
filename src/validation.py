from __future__ import annotations

from datetime import datetime
from typing import Mapping
from urllib.parse import urlparse


REQUIRED_FIELDS = [
    "title",
    "category",
    "price",
    "availability",
    "rating",
    "product_url",
    "image_url",
    "scrape_timestamp",
]


def validate_book_row(row: Mapping[str, str]) -> list[str]:
    errors: list[str] = []

    for field in REQUIRED_FIELDS:
        if not row.get(field):
            errors.append(f"missing {field}")

    if row.get("rating") and row["rating"] not in {"1", "2", "3", "4", "5"}:
        errors.append("rating must be between 1 and 5")

    if row.get("price") and not row["price"].startswith("£"):
        errors.append("price must start with £")

    for field in ["product_url", "image_url"]:
        if row.get(field) and not has_http_url(row[field]):
            errors.append(f"{field} must be an HTTP URL")

    if row.get("scrape_timestamp"):
        try:
            datetime.fromisoformat(row["scrape_timestamp"])
        except ValueError:
            errors.append("scrape_timestamp must be ISO formatted")

    return errors


def has_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
