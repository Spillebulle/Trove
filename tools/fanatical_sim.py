"""Fanatical's guards, without a browser or the network.

The fixtures are the payloads measured on 23 Nov 2026 (plain httpx, Trove's
user agent), trimmed to the fields the adapter reads:

1. **What is a giveaway.** Algolia's `giveaway` flag plus every price zero.
   A price of zero alone is not one: that day `price.USD = 0` matched
   unreleased games with no price yet, free-to-play listings and a prize
   draw (`display_type: "competition"`). A discounted game is never one.
2. **"Free with purchase" is not free.** The `freeProducts` tiers of
   `/api/all-promotions/en` each need a minimum spend or a named product;
   their slugs are excluded even if a record carries the giveaway flag.
3. **The money guard.** The checkout is reached only from a cart holding
   exactly the free game at a total that reads as zero. A mixed cart, a
   priced total or an unreadable cart stops with NeedsAttention and the
   checkout control is never touched; a newsletter-only giveaway is
   not_eligible.

    python tools/fanatical_sim.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
os.environ.setdefault("DATA_DIR", "./data")

import app.adapters.fanatical as fan  # noqa: E402
from app.adapters.base import FreeOffer  # noqa: E402

FAILURES: list[str] = []


def check(name: str, got, expected) -> None:
    ok = got == expected
    print(f"  {'ok  ' if ok else 'FAIL'} {name}: {got!r}")
    if not ok:
        FAILURES.append(f"{name}: expected {expected!r}, got {got!r}")


ZERO = {"GBP": 0, "EUR": 0, "USD": 0, "CAD": 0, "AUD": 0, "RUB": 0, "JPY": 0}

# Measured hits (Algolia `fan` index, 23 Nov 2026), trimmed.
PAID_DISCOUNTED = {
    "name": "Football Manager 27", "slug": "football-manager-27", "type": "game",
    "display_type": "game", "giveaway": False, "product_id": "6aec246ab25ad92c2b2156b6",
    "objectID": "43713575001", "discount_percent": 8, "drm": ["steam"],
    "price": {"GBP": 45.99, "EUR": 55.19, "USD": 55.19},
    "fullPrice": {"GBP": 49.99, "EUR": 59.99, "USD": 59.99},
    "free_to_play": False, "mystery": False, "pay_what_you_want": False,
    "available_valid_from": 1794326400, "available_valid_until": 1796803200,
    "cover": "f4d247ee-495a-4a51-8b2d-4ee8fb43f88b.jpeg",
}
UNPRICED = {  # "price.USD = 0" but simply has no price yet
    **PAID_DISCOUNTED, "name": "Warhammer 40,000: Mechanicus II",
    "slug": "warhammer-40-000-mechanicus-ii", "price": {"USD": 0}, "discount_percent": 0,
}
FREE_TO_PLAY = {**UNPRICED, "name": "World of Tanks", "slug": "world-of-tanks",
                "free_to_play": True, "price": ZERO}
COMPETITION = {  # the measured prize draw
    "name": "Black Friday JSAUX Accessories Giveaway with JSAUX x Fanatical",
    "slug": "black-friday-win-with-jsaux", "type": "competition",
    "display_type": "competition", "giveaway": False, "price": ZERO,
    "product_id": "c1", "free_to_play": False,
}
# No giveaway was live, so this is the measured hit shape with the flag the
# filter selects on turned on and the price zeroed.
GIVEAWAY = {**PAID_DISCOUNTED, "name": "Some Free Game", "slug": "some-free-game",
            "giveaway": True, "price": ZERO, "discount_percent": 100,
            "product_id": "6af000000000000000000001"}

# Measured `freeProducts` tiers (all-promotions), trimmed.
PROMOTIONS = {
    "freeProducts": [
        {"min_spend": {"USD": 1500}, "required_products": [],
         "products": [{"slug": "black-friday-sale-spin-to-win", "price": {"USD": 1500}, "type": "game"}]},
        {"min_spend": {"USD": 0}, "required_products": [{"slug": "dark-horse-40th-anniversary-showcase-collection"}],
         "products": [{"slug": "dark-horse-40th-anniversary-10-off-coupon", "price": ZERO, "type": "book"}]},
        {"min_spend": {"USD": 1592}, "required_products": [{"slug": "supreme-game-surround-sounds-bundle"}],
         "products": [{"slug": "supreme-game-surround-sounds-bundle-2000-download-store-credit", "price": ZERO}]},
    ]
}


def discovery_checks() -> None:
    print("what counts as a giveaway")
    excluded = fan._free_with_purchase_slugs(PROMOTIONS)
    check("free-with-purchase slugs", sorted(excluded), sorted([
        "black-friday-sale-spin-to-win",
        "dark-horse-40th-anniversary-10-off-coupon",
        "supreme-game-surround-sounds-bundle-2000-download-store-credit",
    ]))
    offer = fan._parse_hit(GIVEAWAY, excluded)
    check("a giveaway at zero is found", offer and offer.external_id, "6af000000000000000000001")
    check("its url", offer and offer.url, "https://www.fanatical.com/en/game/some-free-game")
    check("its key store", offer and offer.extra["key_store"], "steam")
    check("its end", offer and offer.ends_at.year, 2026)
    check("discounted but paid", fan._parse_hit(PAID_DISCOUNTED), None)
    check("zero but no price yet", fan._parse_hit(UNPRICED), None)
    check("free to play", fan._parse_hit({**FREE_TO_PLAY, "giveaway": True}), None)
    check("a prize draw", fan._parse_hit({**COMPETITION, "giveaway": True}), None)
    check("giveaway flag but priced", fan._parse_hit({**GIVEAWAY, "price": {"USD": 4.99, "GBP": 0}}), None)
    check("giveaway with one currency missing a price", fan._parse_hit({**GIVEAWAY, "price": {}}), None)
    check("a mystery giveaway", fan._parse_hit({**GIVEAWAY, "mystery": True}), None)
    check(
        "giveaway flag on a free-with-purchase slug",
        fan._parse_hit({**GIVEAWAY, "slug": "black-friday-sale-spin-to-win"}, excluded),
        None,
    )
    check(
        "a name that says 'with every purchase'",
        fan._parse_hit({**GIVEAWAY, "name": "Free game with every purchase over $15"}),
        None,
    )
    check("a giveaway bundle is not guessed at", fan._parse_hit({**GIVEAWAY, "type": "bundle"}), None)


def amount_checks() -> None:
    print("reading an amount")
    for text, expected in [
        ("$0.00", True), ("£0.00", True), ("0,00 €", True), ("FREE", True),
        ("$4.99", False), ("0,01 €", False), ("$10.00", False), ("", None), (None, None), ("—", None),
    ]:
        check(f"{text!r}", fan._is_zero_amount(text), expected)


TITLE = "Some Free Game"


def verdict_checks() -> None:
    print("the money guard (pure)")
    v = fan._cart_verdict
    check("empty before adding", v({"items": [], "empty": True}, TITLE, before_add=True)[0], "empty")
    check("only ours before adding", v({"items": [TITLE]}, TITLE, before_add=True)[0], "ours")
    check("the person's items before adding",
          v({"items": ["Cyberpunk 2077"]}, TITLE, before_add=True)[0], "mixed")
    check("zero total, just the giveaway",
          v({"items": [TITLE], "total": "$0.00"}, TITLE, before_add=False)[0], "proceed")
    check("converted currency also zero",
          v({"items": [TITLE], "total": "£0.00", "charged": "$0.00"}, TITLE, before_add=False)[0], "proceed")
    check("charged in another currency is not zero",
          v({"items": [TITLE], "total": "£0.00", "charged": "$0.50"}, TITLE, before_add=False)[0], "priced")
    check("a non-zero total",
          v({"items": [TITLE], "total": "$4.99"}, TITLE, before_add=False)[0], "priced")
    check("no total at all",
          v({"items": [TITLE], "total": None}, TITLE, before_add=False)[0], "priced")
    check("giveaway plus something else",
          v({"items": [TITLE, "Hades II"], "total": "$0.00"}, TITLE, before_add=False)[0], "mixed")
    check("a different item at zero",
          v({"items": ["Other Free Game"], "total": "$0.00"}, TITLE, before_add=False)[0], "mixed")
    check("unreadable cart", v(None, TITLE, before_add=False)[0], "unreadable")
    check("empty after adding", v({"items": []}, TITLE, before_add=False)[0], "unreadable")


class FakeLoc:
    def __init__(self, page: "FakePage", selector: str) -> None:
        self.page, self.selector = page, selector

    @property
    def first(self):
        return self

    async def is_visible(self) -> bool:
        return self.selector in self.page.visible

    async def click(self, **kw) -> None:
        self.page.clicks.append(self.selector)
        if self.selector in fan.PROCEED or self.selector == "#api-button":
            self.page.url = "https://www.fanatical.com/en/receipt?authResult=AUTHORISED"
            self.page.visible.add(fan.RECEIPT_OK[0])

    async def uncheck(self, **kw) -> None:
        self.page.clicks.append("uncheck " + self.selector)
        self.page.cart["optin_checked"] = False

    async def get_attribute(self, name: str):
        return None


class FakePage:
    def __init__(self, cart: dict, visible: set[str] | None = None) -> None:
        self.cart = cart
        self.visible = set(visible or ())
        self.clicks: list[str] = []
        self.url = fan.CART_URL

    def locator(self, sel: str) -> FakeLoc:
        return FakeLoc(self, sel.split(" >> visible=true")[0])

    async def evaluate(self, script, *args):
        return dict(self.cart)

    async def wait_for_timeout(self, ms: int) -> None:
        pass

    async def goto(self, url: str, **kw):
        self.url = url

    async def wait_for_load_state(self, *a, **kw) -> None:
        pass

    async def inner_text(self, sel: str) -> str:
        return ""


async def checkout_checks() -> None:
    print("the money guard (driving the checkout step)")
    adapter = fan.FanaticalAdapter()

    async def no_shot(*a, **kw):
        return None

    adapter._shot = no_shot
    offer = FreeOffer(external_id="x", title=TITLE, url="https://www.fanatical.com/en/game/some-free-game",
                      extra={"key_store": "steam"})

    async def run(cart: dict, visible=None) -> tuple[str, list[str]]:
        page = FakePage(cart, visible=(visible or set()) | {fan.PROCEED[0]})
        try:
            result = await adapter._checkout_from_cart(page, offer, cart, None)
            verdict = f"{result.outcome}:{result.reason_code or ''}"
        except fan.NeedsAttention:
            verdict = "attention"
        touched = [c for c in page.clicks if c in fan.PROCEED or c == "#api-button"]
        return verdict, touched

    verdict, touched = await run({"items": [TITLE, "Hades II"], "total": "$0.00"})
    check("mixed cart: stops, checkout untouched", (verdict, touched), ("attention", []))
    verdict, touched = await run({"items": [TITLE], "total": "$4.99"})
    check("priced cart: stops, checkout untouched", (verdict, touched), ("attention", []))
    verdict, touched = await run(None)  # type: ignore[arg-type]
    check("unreadable cart: stops, checkout untouched", (verdict, touched), ("attention", []))
    verdict, touched = await run({
        "items": [TITLE], "total": "$0.00",
        "warnings": ["The giveaway in your cart is only available to newsletter subscribers."],
    })
    check("newsletter-only giveaway: not eligible, untouched",
          (verdict, touched), ("not_eligible:newsletter_required", []))

    page = FakePage({"items": [TITLE], "total": "$0.00", "optin_checked": True},
                    visible={fan.PROCEED[0], fan.NEWSLETTER_OPTIN})
    try:
        await adapter._checkout_from_cart(page, offer, dict(page.cart), None)
    except fan.NeedsAttention:
        pass
    check("a pre-ticked opt-in is unticked before the one checkout click",
          page.clicks[:2], [f"uncheck {fan.NEWSLETTER_OPTIN}", fan.PROCEED[0]])

    page = FakePage({"items": [TITLE], "total": "$0.00"}, visible={fan.PROCEED[0]})
    try:
        result = await adapter._checkout_from_cart(page, offer, dict(page.cart), None)
        verdict = result.outcome
    except fan.NeedsAttention as exc:
        verdict = f"attention: {exc.reason}"
    check("a clean zero cart checks out once and claims", (verdict, page.clicks.count(fan.PROCEED[0])),
          ("claimed", 1))


async def product_checks() -> None:
    print("the giveaway's own conditions")
    c = fan._product_conditions
    check("newsletter giveaway", (c({"giveaway": True, "price": ZERO, "giveaway_newsletter": True}) or ())[:2],
          ("not_eligible", "newsletter_required"))
    check("steam wishlist giveaway", (c({"giveaway": True, "price": ZERO, "giveaway_wishlist_on_steam": True}) or ())[:2],
          ("not_eligible", "steam_wishlist_required"))
    check("ended giveaway", (c({"giveaway": False, "price": {"USD": 9.99}}) or ())[:1], ("failed",))
    check("plain giveaway carries on", c({"giveaway": True, "price": ZERO}), None)
    check("unreadable record carries on", c(None), None)


async def main() -> int:
    discovery_checks()
    amount_checks()
    verdict_checks()
    await checkout_checks()
    await product_checks()
    print()
    if FAILURES:
        for failure in FAILURES:
            print("FAILED:", failure)
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
