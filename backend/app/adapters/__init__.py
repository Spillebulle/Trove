"""The adapter registry: one place a store is registered.

HomeLab Manager's `ADAPTER_MAP` is the model. A new store is a new module here
and one line in the table, and nothing else in the app learns its name.

Epic came first and alone, on purpose: CLAUDE.md required one store, one
account, one full loop, proven, before a second adapter existed. That loop
closed on 20 Aug 2026 (a real game claimed end to end), so Steam - the
second store - now has an adapter. It claims only the rare "free to keep"
promotions; see its module docstring for what that means and what it skips.
"""
from __future__ import annotations

from .base import BaseAdapter, ClaimResult, FreeOffer, Requirement
from .epic import EpicAdapter
from .steam import SteamAdapter

ADAPTER_MAP: dict[str, type[BaseAdapter]] = {
    "epic": EpicAdapter,
    "steam": SteamAdapter,
}

__all__ = [
    "ADAPTER_MAP",
    "BaseAdapter",
    "ClaimResult",
    "FreeOffer",
    "Requirement",
    "get_adapter",
    "known_stores",
]


def get_adapter(store: str) -> BaseAdapter:
    cls = ADAPTER_MAP.get(store)
    if cls is None:
        raise ValueError(f"Unknown store {store!r}")
    return cls()


def known_stores() -> list[dict]:
    """Every store the app can drive, for the add-account page.

    Includes what each adapter declares it needs, so the page can explain what
    it is asking for rather than showing a form of mysteries.
    """
    stores = []
    for key, cls in ADAPTER_MAP.items():
        adapter = cls()
        stores.append(
            {
                "store": key,
                "display_name": adapter.display_name,
                "blurb": adapter.blurb,
                "login_url": adapter.login_url,
                "requirements": [
                    {
                        "name": requirement.name,
                        "description": requirement.description,
                        "required": requirement.required,
                    }
                    for requirement in adapter.requirements()
                ],
            }
        )
    return stores
