"""Ubisoft's giveaway discovery and claim guards, without a browser or network.

The fixtures are the shapes measured on the live store, 23 Nov 2026 - search
hits trimmed to the fields the adapter reads, a free-offers tile, the
`window.SitePreferences` line and the game page's offer payload. No giveaway
was running that day, so the giveaway hits are the measured record shape with
`Free_offer` set to the value the store's own tile renderer badges as a
giveaway ("giveaway"); everything else is verbatim.

What must hold:

1. **Only `Free_offer: giveaway` is free to keep.** Free-to-play (`freeplay`,
   which free weekends share), demos, free-with-purchase and a bare $0 price
   are all refused - the measured index has $0 coming-soon rows and a $0 PS5
   edition with no offer at all.
2. **Unreleased, pre-order and console-only giveaways are refused.**
3. **The money guard** clicks only a `giveaway`-classed button whose label
   carries no purchase verb and no non-zero price.
4. **The game page's own `offerType`** is what says a giveaway is on.

    python tools/ubisoft_sim.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="trove-ubisoft-sim-"))

import app.adapters.ubisoft as ubisoft  # noqa: E402

FAILURES: list[str] = []


def check(name: str, got, expected) -> None:
    ok = got == expected
    print(f"  {'ok  ' if ok else 'FAIL'} {name}: {got!r}")
    if not ok:
        FAILURES.append(f"{name}: expected {expected!r}, got {got!r}")


# ── Measured search hits (trimmed) ──────────────────────────────────────────

BRAWLHALLA = {  # free to play, measured
    "id": "608178d65cdf9a2e6c5948fd", "objectID": "608178d65cdf9a2e6c5948fd",
    "MasterID": "6077f2aa5cdf9a268c10d3dd", "title": "Brawlhalla",
    "Free_offer": "freeplay", "Edition": "Free to Play", "Platform": "PC (Digital)",
    "price": {"USD": 0}, "product_type": "Games", "comingSoon": "No", "preorder": "false",
    "link": "https://store.ubisoft.com/us/brawlhalla/608178d65cdf9a2e6c5948fd.html?lang=en_US",
}
OUTLAWS_DEMO = {  # demo, measured
    "id": "686fcabbcdbd237b1001ea75", "MasterID": "645ba713a9ce0448bffa4c12",
    "title": "Star Wars Outlaws", "Free_offer": "demo", "Platform": "Switch 2 (Digital)",
    "platforms_availability": "[\"PC (Digital)\",\"PS5 (Digital)\",\"Xbox (Digital)\",\"Steam\",\"Switch 2 (Digital)\"]",
    "price": {"USD": 0}, "product_type": "Games", "comingSoon": "No", "preorder": "false",
}
MORBID_METAL = {  # $0 because it has no price yet, measured
    "id": "693af554f7adb16cf95c7960", "MasterID": "693af50af7adb16cf95c795f",
    "title": "Morbid Metal", "Free_offer": "", "Platform": "PC (Digital)",
    "price": {"USD": 0}, "product_type": "Games", "comingSoon": "Yes", "preorder": "false",
}
AVATAR_PS5 = {  # $0, released, no offer: a console edition, measured
    "id": "68dad1ab4dc7735d4c2b57d7", "MasterID": "68c98ac33dddae27ae2268aa",
    "title": "Avatar: Frontiers of Pandora From the Ashes Edition", "Free_offer": "",
    "Platform": "PS5 (Digital)", "price": {"USD": 0}, "product_type": "Games",
    "comingSoon": "No", "preorder": "false",
}
RAYMAN_PAID = {  # an ordinary paid game, measured
    "id": "69d81be7c446d004fbdc1525", "title": "Rayman Legends Retold", "Free_offer": "",
    "Platform": "PC (Digital)", "price": {"USD": 39.99}, "product_type": "Games",
    "comingSoon": "No", "preorder": "false",
}
FREE_WITH_PURCHASE = dict(RAYMAN_PAID, id="69d81be7c446d004fbdc1599", Free_offer="freeWithPurchase")

# The measured record shape with the store's giveaway value.
GIVEAWAY = {
    "id": "5704fac688a7e32b078b4674", "objectID": "5704fac688a7e32b078b4674",
    "MasterID": "56c494ad88a7e300458b4d5a", "title": "Rayman Legends",
    "Free_offer": "giveaway", "Edition": "Standard Edition", "Platform": "PC (Digital)",
    "price": {"USD": 19.99}, "product_type": "Games", "comingSoon": "No", "preorder": "false",
    "link": "https://store.ubisoft.com/us/rayman-legends/5704fac688a7e32b078b4674.html?lang=en_US",
    "image_link": "https://store.ubisoft.com/dw/image/v2/ABBS_PRD/x.jpg",
}
GIVEAWAY_CAPS = dict(GIVEAWAY, id="5704fac688a7e32b078b4675", Free_offer="Giveaway")
GIVEAWAY_DLC = dict(GIVEAWAY, id="5704fac688a7e32b078b4676", product_type="DLCs", title="A Pack")
GIVEAWAY_SOON = dict(GIVEAWAY, id="5704fac688a7e32b078b4677", comingSoon="Yes")
GIVEAWAY_PREORDER = dict(GIVEAWAY, id="5704fac688a7e32b078b4678", preorder="true")
GIVEAWAY_CONSOLE = dict(GIVEAWAY, id="5704fac688a7e32b078b4679", Platform="PS5 (Digital)")
GIVEAWAY_NO_ID = dict(GIVEAWAY, id="", objectID="")

# A free-offers tile, verbatim apart from whitespace and the trimmed image
# block, plus a second tile carrying the store's giveaway badge class.
TILE_HTML = """
<li class="grid-tile cell shrink splide__slide c-tile--web">
<div tabindex="0" class="product-tile card   "
id="f0f21dfc7c39641d8763384da0"
data-brand="Roller Champions"
data-itemid="5cf15f4f39798c0870c076e7"
data-mdmbid="2552"
data-mdmiid="6074"
 >
<a class="thumb-link" href="/us/roller-champions/5cf15f4f39798c0870c076e7.html?lang=en_US"></a>
<div class="card-additional-details card-labels-wrapper"></div>
<div class="product-availability-label" data-comingsoon="false" data-freeplay="true" data-freeofferstartdate="Mon Jun 10 21:15:00 GMT 2019" data-freeofferenddate="Fri May 24 13:00:00 GMT 2030" data-dlc="" data-ismobileproduct="false"></div>
<span class="price-sales standard-price">$0.00</span>
</div></li>
<li class="grid-tile cell shrink splide__slide c-tile--web">
<div tabindex="0" class="product-tile card   "
data-itemid="56c494ad88a7e300458b4d5a"
 >
<div class="card-additional-details card-labels-wrapper"><div class="card-label giveaway">giveaway</div></div>
<div class="product-availability-label" data-comingsoon="false" data-freeplay="false" data-freeofferstartdate="Thu Nov 19 15:00:00 GMT 2026" data-freeofferenddate="Thu Nov 26 15:00:00 GMT 2026" data-dlc="" data-ismobileproduct="false"></div>
</div></li>
"""

SITE_PREFS_HTML = (
    '<script>\nwindow.Urls = {"x":1};\n'
    'window.SitePreferences = {"SEARCH_ENGINE_ID":"algolia","ALGOLIA_API_ID":"XELY3U4LOD",'
    '"ALGOLIA_API_KEY":"5638539fd9edb8f2c6b024b49ec375bd","ALGOLIA_INDEXES_V2":"{\\n  \\"en_US\\": '
    '{\\n    \\"defaultIndex\\": \\"production__us_ubisoft__products__en_US__best_sellers\\"\\n  },'
    '\\n  \\"default\\": {\\n    \\"defaultIndex\\": \\"production__us_ubisoft__products__default__best_sellers\\"\\n  }\\n}"};\n'
    "window.exposedData = {};\n</script>"
)

# The game page's RSC payload as it sits in the script text: measured (null)
# and the same object with the store's giveaway values.
GAME_PAGE_NULL = r'{\"gameTitle\":\"Rayman Legends Retold\",\"offerType\":null,\"offerEnds\":null,\"offerId\":null}'
GAME_PAGE_GIVEAWAY = r'{\"gameTitle\":\"Rayman Legends\",\"offerType\":\"GIVEAWAY\",\"offerEnds\":\"2026-11-26T15:00:00.000Z\",\"offerId\":\"123\"}'
GAME_PAGE_WEEKEND = r'{\"gameTitle\":\"X\",\"offerType\":\"FREE_WEEKEND\",\"offerEnds\":\"2026-11-24T18:00:00.000Z\"}'


def discovery_checks() -> None:
    print("what counts as free to keep")
    for name, hit in [
        ("free to play is refused", BRAWLHALLA),
        ("a demo is refused", OUTLAWS_DEMO),
        ("$0 coming soon is refused", MORBID_METAL),
        ("$0 console edition is refused", AVATAR_PS5),
        ("a paid game is refused", RAYMAN_PAID),
        ("free with purchase is refused", FREE_WITH_PURCHASE),
        ("a giveaway not yet released is refused", GIVEAWAY_SOON),
        ("a giveaway on pre-order is refused", GIVEAWAY_PREORDER),
        ("a console-only giveaway is refused", GIVEAWAY_CONSOLE),
        ("a giveaway with no id is refused", GIVEAWAY_NO_ID),
    ]:
        check(name, ubisoft.parse_hit(hit), None)

    tiles = ubisoft.parse_tiles(TILE_HTML)
    offer = ubisoft.parse_hit(GIVEAWAY, tiles)
    check(
        "a giveaway is found, with its identity",
        (offer.external_id, offer.title, offer.kind) if offer else None,
        ("5704fac688a7e32b078b4674", "Rayman Legends", "game"),
    )
    check(
        "its end date comes from the tile, matched on MasterID",
        offer.ends_at if offer else None,
        datetime(2026, 11, 26, 15, 0, tzinfo=timezone.utc),
    )
    check("the value is case-insensitive", bool(ubisoft.parse_hit(GIVEAWAY_CAPS)), True)
    dlc = ubisoft.parse_hit(GIVEAWAY_DLC)
    check("a DLC giveaway is kind dlc", dlc.kind if dlc else None, "dlc")

    print("the free page")
    check(
        "tiles: end dates and the giveaway badge",
        {k: (v["ends_at"].year if v["ends_at"] else None, v["giveaway"]) for k, v in tiles.items()},
        {"5cf15f4f39798c0870c076e7": (2030, False), "56c494ad88a7e300458b4d5a": (2026, True)},
    )
    check(
        "search credentials from SitePreferences",
        ubisoft.parse_site_preferences(SITE_PREFS_HTML),
        ("XELY3U4LOD", "5638539fd9edb8f2c6b024b49ec375bd",
         "production__us_ubisoft__products__en_US__best_sellers"),
    )
    check("no SitePreferences is None", ubisoft.parse_site_preferences("<html></html>"), None)
    check("an unreadable date is None", ubisoft.parse_java_date("soon"), None)


def guard_checks() -> None:
    print("the money guard")
    cases = [
        ("the giveaway button", "button add-to-cart giveaway", "Get it for free", True),
        ("the giveaway button showing $0.00", "add-to-cart giveaway", "Get it for free $0.00", True),
        ("the giveaway button, \"ADD TO MY GAMES\"", "giveaway", "ADD TO MY GAMES", True),
        ("the paid add-to-cart", "button add-to-cart", "Add To Cart", False),
        ("giveaway class but a cart label", "add-to-cart giveaway", "Add To Cart", False),
        ("giveaway class but a price", "giveaway", "Get it $19.99", False),
        ("giveaway class but a krone price", "giveaway", "Get it 179,00 kr", False),
        ("a pre-order", "giveaway", "Pre-order", False),
        ("a class merely containing the word", "not-a-giveaway-button", "Get it", False),
    ]
    for name, classes, text, expected in cases:
        check(name, ubisoft.is_free_button(classes, text), expected)

    print("ownership, read only as a yes")
    check("listed id", ubisoft.ownership_has(["5704fac688a7e32b078b4674"], {"5704fac688a7e32b078b4674"}), True)
    check("dict with the master id", ubisoft.ownership_has({"owned": ["56c494ad88a7e300458b4d5a"]}, {"56c494ad88a7e300458b4d5a"}), True)
    check("null (signed out, measured)", ubisoft.ownership_has(None, {"5704fac688a7e32b078b4674"}), False)
    check("not listed", ubisoft.ownership_has(["x"], {"5704fac688a7e32b078b4674"}), False)

    print("the game page's offer type")
    check("measured page: no offer", ubisoft._OFFER_TYPE_RE.findall(GAME_PAGE_NULL), [])
    check("giveaway page", ubisoft._OFFER_TYPE_RE.findall(GAME_PAGE_GIVEAWAY), ["GIVEAWAY"])
    check("free weekend page is not a giveaway", "GIVEAWAY" in ubisoft._OFFER_TYPE_RE.findall(GAME_PAGE_WEEKEND), False)
    ends = ubisoft._OFFER_ENDS_RE.search(GAME_PAGE_GIVEAWAY)
    check("offerEnds read", ends.group(1) if ends else None, "2026-11-26T15:00:00.000Z")


class FakeResponse:
    def __init__(self, text: str = "", payload: dict | None = None) -> None:
        self.text, self._payload = text, payload or {}

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._payload


class FakeClient:
    """Stands in for httpx: the free page, then one search answer."""

    hits: list = []
    posted: list = []

    def __init__(self, *a, **kw) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a) -> bool:
        return False

    async def get(self, url, params=None) -> FakeResponse:
        return FakeResponse(text=SITE_PREFS_HTML + TILE_HTML)

    async def post(self, url, headers=None, json=None) -> FakeResponse:
        FakeClient.posted.append((url, headers.get("X-Algolia-API-Key"), json))
        return FakeResponse(payload={"hits": FakeClient.hits, "nbHits": len(FakeClient.hits)})


async def list_checks() -> None:
    print("list_free_offers end to end")
    real_client, real_sleep = ubisoft.httpx.AsyncClient, ubisoft.asyncio.sleep

    async def no_sleep(*a, **kw):
        return None

    ubisoft.httpx.AsyncClient = FakeClient
    ubisoft.asyncio.sleep = no_sleep
    try:
        FakeClient.hits = [BRAWLHALLA, GIVEAWAY, MORBID_METAL]
        offers = await ubisoft.UbisoftAdapter().list_free_offers()
        check("only the giveaway survives", [o.external_id for o in offers], ["5704fac688a7e32b078b4674"])
        url, key, body = FakeClient.posted[-1]
        check(
            "asks the US index with the page's key and the giveaway facet",
            (url.endswith("production__us_ubisoft__products__en_US__best_sellers/query"), key,
             body["facetFilters"]),
            (True, "5638539fd9edb8f2c6b024b49ec375bd", [["Free_offer:giveaway"]]),
        )
        FakeClient.hits = []
        check("nothing on: an empty list", await ubisoft.UbisoftAdapter().list_free_offers(), [])
    finally:
        ubisoft.httpx.AsyncClient, ubisoft.asyncio.sleep = real_client, real_sleep


async def main() -> int:
    discovery_checks()
    guard_checks()
    await list_checks()
    print()
    if FAILURES:
        for failure in FAILURES:
            print("FAILED:", failure)
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
