"""Internal boundary only. Live API access is coordinated with the product owner."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


class CapabilityNotAvailable(Exception):
    pass


@dataclass(frozen=True)
class ConnectionResult:
    available: bool
    reason: str


@dataclass(frozen=True)
class ExternalOrder:
    external_order_no: str
    status: str
    occurred_at: datetime
    paid_amount_fen: int | None


@dataclass(frozen=True)
class OrderPage:
    orders: tuple[ExternalOrder, ...]
    next_cursor: str | None


class MarketplaceAdapter(Protocol):
    def verify_connection(self) -> ConnectionResult: ...

    def pull_orders(self, cursor: str | None, since: datetime | None) -> OrderPage: ...

    def get_order(self, external_order_no: str) -> ExternalOrder: ...


class DisabledMarketplaceAdapter:
    def verify_connection(self) -> ConnectionResult:
        return ConnectionResult(False, "接口接入未启用；真实连接需与产品负责人配合。")

    def pull_orders(self, cursor: str | None = None, since: datetime | None = None) -> OrderPage:
        raise CapabilityNotAvailable("CAPABILITY_NOT_AVAILABLE")

    def get_order(self, external_order_no: str) -> ExternalOrder:
        raise CapabilityNotAvailable("CAPABILITY_NOT_AVAILABLE")
