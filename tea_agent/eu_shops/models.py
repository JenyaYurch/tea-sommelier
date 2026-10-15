"""Shared records for the price-index refresh. No network."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class FetchResponse:
    """One HTTP response, already decoded when the body was JSON."""

    status: int
    headers: dict[str, str]
    data: Any
    error: str | None = None


@dataclass(frozen=True)
class Shop:
    """One configured store. ``currency`` is only a fallback."""

    id: str
    name: str
    platform: str
    base_url: str
    country: str
    currency: str | None = None
    parser: str | None = None
    listing_urls: tuple[str, ...] = ()


@dataclass
class ShopResult:
    """Outcome of reading one shop. Offers are empty when ``status`` is error."""

    shop_id: str
    name: str
    platform: str
    status: str
    offers: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    warnings: list[str] = field(default_factory=list)
    pages: int = 0

    def report(self) -> dict[str, Any]:
        in_stock = sum(1 for offer in self.offers if offer.get("in_stock") is True)
        unclear = sum(1 for offer in self.offers if offer.get("pack_size_unclear") is True)
        by_category = Counter(
            str(offer.get("category"))
            for offer in self.offers
            if offer.get("category")
        )
        return {
            "id": self.shop_id,
            "name": self.name,
            "platform": self.platform,
            "status": self.status,
            "offers": len(self.offers),
            "in_stock": in_stock,
            "unclear_pack": unclear,
            "by_category": dict(sorted(by_category.items())),
            "pages": self.pages,
            "error": self.error,
            "warnings": list(self.warnings),
        }
