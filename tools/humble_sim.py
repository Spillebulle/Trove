"""Drive the Humble Bundle adapter with no browser and no network.

Fixtures are the shapes measured off the live site, 23 Nov 2026 (see the
module docstring of `backend/app/adapters/humble.py`): Next.js flight chunks
holding `algolia-hit` product feed entries, the product page's own
`displayPrice`, and the membership page's `humble-choice` items. The flight is
re-encoded the way the site sends it (`self.__next_f.push([1, "..."])`) so
the parser is tested against the real wrapping, not a convenience.

  1. Store discovery keeps only a temporarily-free product: zero now and a
     positive base price or a sale. Paid, discounted, permanently free, a
     bundle, and a float price of 1998.9999 are all rejected.
  2. Choice parsing: month from the meta text, items prefixed with it, an
     `external_key` item (IGN Plus) skipped, delivery mapped to key_store.
  3. Small parsers: subscriber flag, product price, checkout totals.
  4. In-session discovery: a subscriber gets store + Choice offers; a
     non-subscriber (redirected to /membership) gets only the store offers
     and a log line saying so; a month behind "Pay to Unlock" yields nothing.
  5. The Choice claim path on a fake library: an unrevealed game is pressed
     exactly once and its key comes back; an already-revealed one is
     `already_owned` with its key and no click; a Battle.net one is
     `link_required` with no click; a platform dialog is cancelled and
     recorded; a locked month is `not_unlocked` with no click.
  6. The store claim's money guard: a non-zero checkout total presses no order
     button; a newsletter gate presses nothing; a zero total presses the order
     button exactly once.

    python tools/humble_sim.py
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="trove-humble-sim-"))

import app.adapters.humble as humble  # noqa: E402
from app.adapters.base import FreeOffer  # noqa: E402
from app.browser import NeedsAttention  # noqa: E402

OK = True


def check(cond: bool, what: str) -> None:
    global OK
    print(("  ok   " if cond else "  FAIL ") + what)
    if not cond:
        OK = False


def page_html(flight: str, head: str = "") -> str:
    """Wrap a flight string the way the site does: split across pushes."""
    half = len(flight) // 2
    pushes = "".join(
        f'<script>self.__next_f.push([1,{json.dumps(part)}])</script>'
        for part in (flight[:half], flight[half:])
    )
    return f"<html><head>{head}</head><body>{pushes}</body></html>"


def price(cents, currency="EUR"):
    return {"type": "centPrecision", "centAmount": cents, "currencyCode": currency, "fractionDigits": 2}


def hit(pid, key, title, current, base, sale=False, ptype="storefront-game-product", until="$undefined"):
    # The measured algolia-hit shape, trimmed of fields the parser ignores.
    return {
        "relationTo": "algolia-hit",
        "value": {
            "id": pid, "productKey": key, "slug": key, "url": f"/store/{key}",
            "title": title,
            "thumbnail": f"https://hb-prd.imgix.net/product-media/{key}.jpg",
            "deliveryMethods": ["steam"], "platforms": ["windows"], "tag": "$undefined",
            "displayPrice": {
                "currentPrice": price(current), "basePrice": price(base),
                "isSale": sale, "country": "US", "validUntil": until,
                "sku": f"{key}_storefront_steam", "choiceDiscountEligible": True,
                "taxIncluded": True,
            },
            "endDate": "$undefined", "itemsCount": 0, "productType": ptype,
        },
    }


STORE_FLIGHT = (
    '77:["$","$L80","6af6079d8bbe054b7221873f",{"feedItems":'
    + json.dumps([
        hit("aaaaaaaa-0000-0000-0000-000000000001", "free-game", "Free Game", 0, 1999, sale=True,
            until="2026-11-30T17:00:00.000Z"),
        hit("aaaaaaaa-0000-0000-0000-000000000002", "death-ray-manta", "Death Ray Manta", 499, 499),
        hit("aaaaaaaa-0000-0000-0000-000000000003", "sayl", "Sayl Tides of Torment", 602, 899, sale=True),
        hit("aaaaaaaa-0000-0000-0000-000000000004", "always-free", "Always Free", 0, 0),
        hit("aaaaaaaa-0000-0000-0000-000000000005", "some_bundle", "Some Bundle", 0, 445,
            sale=True, ptype="bundle"),
        hit("aaaaaaaa-0000-0000-0000-000000000006", "dysmantle", "Float Price", 1998.9999999999998, 1999),
        # The same free product featured twice on one page: one offer.
        hit("aaaaaaaa-0000-0000-0000-000000000001", "free-game", "Free Game", 0, 1999, sale=True),
    ], separators=(",", ":"))
    + "}]\n"
)


def choice(cid, title, delivery, end="2027-06-01T00:00:00+00:00"):
    return {
        "id": cid, "productType": "humble-choice",
        "thumbnail": f"https://hb-prd.imgix.net/product-media/{cid}.jpg",
        "title": title, "description": "$78", "url": "",
        "displayPrice": {"isSale": False, "currentPrice": price(1999)},
        "reviewScore": "$undefined", "genres": "$undefined",
        "deliveryMethods": [delivery], "platforms": ["windows"],
        "developers": [{"name": "Dev", "url": "http://example.com/"}],
        "endDate": end,
    }


CHOICE_ITEMS = [
    choice("dysmantle_choice", "DYSMANTLE", "steam"),
    choice("theouterworlds2_choice", "The Outer Worlds 2", "blizzard"),
    choice("spacehaven_choice", "Space Haven", "steam"),
    choice("ignplus_choice_2026_11", "IGN Plus Subscription", "external_key", "2027-01-01T00:00:00+00:00"),
]
MEMBERSHIP_HEAD = (
    '<meta name="description" content="Get November 2026 Humble Choice and more '
    'when you subscribe for just $11.99 per month!"/>'
)


def membership_flight(subscriber: bool | None) -> str:
    flag = "" if subscriber is None else (
        f'["$","$L6d",null,{{"cart":null,"hasActiveSubscription":{json.dumps(subscriber)}}}]\n'
    )
    return (
        flag
        + '18:["$","$L72","x",{"blockName":"Choice Carousel","blockType":"choice-media-carousel",'
        + '"items":' + json.dumps(CHOICE_ITEMS, separators=(",", ":")) + "}]\n"
    )


PRODUCT_FLIGHT_FREE = (
    'a:[false,["$","$L71",null,{"data":{"storePage":{"key":"free-game","id":"aaaaaaaa-0000-0000-0000-000000000001",'
    '"title":"Free Game","sku":"freegame_storefront_steam","purchaseLimit":2},'
    '"displayPrice":{"sku":"freegame_storefront_steam","currentPrice":'
    + json.dumps(price(0)) + ',"basePrice":"$a:1:props:data:displayPrice:currentPrice",'
    '"isSale":true,"validUntil":"$undefined","taxIncluded":true},"userId":"u1"}}]]\n'
)
PRODUCT_FLIGHT_PAID = PRODUCT_FLIGHT_FREE.replace('"centAmount": 0', '"centAmount": 499')


# ── 1-3: the parsers ────────────────────────────────────────────────────────


def test_parsers() -> None:
    print("store discovery")
    hits = humble.parse_store_hits(page_html(STORE_FLIGHT))
    check(len(hits) == 7, f"seven feed entries parsed out of the flight (got {len(hits)})")
    offers = humble.free_offers_from_hits(hits)
    titles = [o.title for o in offers]
    check(titles == ["Free Game"], f"only the temporarily-free product kept: {titles}")
    if offers:
        o = offers[0]
        check(o.external_id == "aaaaaaaa-0000-0000-0000-000000000001", "identity is the product UUID, not the slug")
        check(o.url == "https://www.humblebundle.com/store/free-game", f"absolute product URL: {o.url}")
        check(o.ends_at is not None and o.ends_at.isoformat().startswith("2026-11-30T17:00"), f"ends_at from validUntil: {o.ends_at}")
        check(o.extra.get("delivery") == ["steam"], "delivery carried for the key's store")
    check(humble.parse_store_hits(page_html("nothing here")) == [], "no feed -> empty, not a crash")

    print("choice parsing")
    html = page_html(membership_flight(True), MEMBERSHIP_HEAD)
    items = humble.parse_choice_items(html)
    check([i["id"] for i in items] == [c["id"] for c in CHOICE_ITEMS], f"all four Choice items parsed: {[i['id'] for i in items]}")
    month = humble.parse_choice_month(html)
    check(month == "2026-11", f"month from the meta text: {month}")
    choice_offers = humble.choice_offers_from_items(items, month)
    ids = [o.external_id for o in choice_offers]
    check(ids == ["choice:2026-11:dysmantle_choice", "choice:2026-11:theouterworlds2_choice",
                  "choice:2026-11:spacehaven_choice"], f"IGN Plus skipped, ids month-scoped: {ids}")
    check(humble._key_store(choice_offers[0]) == "steam", "steam delivery -> key_store steam")
    check(humble._key_store(choice_offers[1]) == "battlenet", "blizzard delivery -> key_store battlenet")
    check(all(humble._is_choice(o) for o in choice_offers), "choice offers are recognised as Choice")

    print("small parsers")
    check(humble.parse_subscriber(page_html(membership_flight(True))) is True, "subscriber flag true")
    check(humble.parse_subscriber(page_html(membership_flight(False))) is False, "subscriber flag false (the measured signed-out value)")
    check(humble.parse_subscriber(page_html(membership_flight(None))) is None, "no flag -> None, not False")
    check(humble.parse_product_price(page_html(PRODUCT_FLIGHT_FREE)) == 0, "product page price 0")
    check(humble.parse_product_price(page_html(PRODUCT_FLIGHT_PAID)) == 499, "product page price 499")
    check(humble.parse_product_price("<html></html>") is None, "no price -> None")
    for text, want in [
        ("Subtotal €0.00\nTotal €0.00", True),
        ("Order summary\nTotal: $0", True),
        ("Subtotal €0.00\nTotal €4.99", False),
        ("Total €0.00\nTotal savings €19.99", False),  # the last Total decides; fails safe
        ("Total 1,234.56", False),
        ("Your cart", None),
        ("Subtotal €0.00", None),
    ]:
        check(humble.total_is_zero(text) is want, f"total_is_zero({text!r}) is {want}")


# ── 4-6: fake pages ─────────────────────────────────────────────────────────


def _bare(sel: str) -> str:
    return sel.split(" >> visible=true")[0]


class FakeLoc:
    def __init__(self, name, state, card=None):
        self.name, self.state, self.card = name, state, card

    async def inner_text(self):
        return self.state.get("dialog_text", self.name) if self.name == "DIALOG" else self.name

    async def click(self, timeout=0):
        self.state["clicks"].append((self.card.title if self.card else "page", self.name))
        on_click = self.state.get("on_click", {}).get(self.name)
        if on_click:
            on_click(self)

    async def fill(self, value):
        self.state["filled"].append(value)

    async def evaluate(self, js):
        return self.state.get("optin_required", False)


class FakeCard:
    def __init__(self, title, text, buttons):
        self.title, self.text, self.buttons = title, text, set(buttons)


class FakePage:
    def __init__(self, state):
        self.state = state
        self.url = "https://www.humblebundle.com/"

    async def wait_for_timeout(self, ms):
        pass


def install(state) -> None:
    names = {
        id(humble.CHALLENGE): "CHALLENGE", id(humble.LOGIN_PROMPT): "LOGIN",
        id(humble.PAY_EARLY): "PAY_EARLY", id(humble.LIBRARY_SEARCH): "SEARCH",
        id(humble.CARD_REVEAL): "REVEAL", id(humble.CARD_LINK): "LINK",
        id(humble.PLATFORM_DIALOG): "PLATFORM", id(humble.DIALOG_CANCEL): "CANCEL",
        id(humble.DIALOG): "DIALOG", id(humble.REDEEMED_TOAST): "TOAST",
        id(humble.ADD_TO_CART): "ADD_TO_CART", id(humble.GET_FREE): "GET_FREE",
        id(humble.NEWSLETTER_OPTIN): "OPTIN", id(humble.PLACE_ORDER): "PLACE_ORDER",
    }

    async def first_visible(target, selectors, timeout_ms=0):
        name = names.get(id(selectors))
        if isinstance(target, FakeCard):
            return FakeLoc(name, state, target) if name in target.buttons else None
        return FakeLoc(name, state) if name in state["visible"] else None

    async def goto(page, url):
        state["gotos"].append(url)
        page.url = state.get("redirects", {}).get(url, url)
        return None

    async def page_html(page):
        return state.get("html", {}).get(page.url, "")

    async def body_text(page):
        return state.get("body", "")

    async def find_card(page, title):
        return state.get("cards", {}).get(title)

    async def card_text(card):
        return card.text

    humble._first_visible = first_visible
    humble._goto = goto
    humble._page_html = page_html
    humble._body_text = body_text
    humble._find_card = find_card
    humble._card_text = card_text


def adapter():
    a = humble.HumbleAdapter()

    async def noshot(page, offer, tag):
        return f"{tag}.png"

    a._shot = noshot
    return a


def new_state(**kw):
    state = {"clicks": [], "gotos": [], "filled": [], "visible": set()}
    state.update(kw)
    return state


class Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(record.getMessage())


async def test_discovery() -> None:
    print("in-session discovery")
    store_offer = humble.free_offers_from_hits(humble.parse_store_hits(page_html(STORE_FLIGHT)))
    humble._public_cache = (humble.time.monotonic(), store_offer)  # no network

    home = humble.MEMBERSHIP_HOME_URL
    state = new_state(html={home: page_html(membership_flight(True), MEMBERSHIP_HEAD)})
    install(state)
    offers = await adapter().discover_in_session(FakePage(state))
    ids = [o.external_id for o in offers]
    check(ids[0] == "aaaaaaaa-0000-0000-0000-000000000001" and len(ids) == 4,
          f"subscriber: the store giveaway plus three Choice games: {ids}")
    check(not state["clicks"], "discovery clicks nothing")

    # Non-subscriber: /membership/home redirects to /membership (the measured
    # signed-out behaviour; assumed for a signed-in non-subscriber).
    cap = Capture()
    humble.logger.addHandler(cap)
    humble.logger.setLevel(logging.INFO)
    state = new_state(
        redirects={home: humble.MEMBERSHIP_URL},
        html={humble.MEMBERSHIP_URL: page_html(membership_flight(False), MEMBERSHIP_HEAD)},
    )
    install(state)
    offers = await adapter().discover_in_session(FakePage(state))
    check([o.external_id for o in offers] == ["aaaaaaaa-0000-0000-0000-000000000001"],
          "non-subscriber: only the store giveaway, no Choice offers")
    check(any("not a subscriber" in line for line in cap.lines), "non-subscriber: a log line says so")

    state = new_state(html={home: page_html(membership_flight(True), MEMBERSHIP_HEAD)}, visible={"PAY_EARLY"})
    install(state)
    offers = await adapter().discover_in_session(FakePage(state))
    check(len(offers) == 1 and not state["clicks"], "locked month (Pay to Unlock): no Choice offers, nothing pressed")
    check(any("never pays early" in line for line in cap.lines), "locked month: a log line says so")

    state = new_state(html={home: page_html(membership_flight(None), MEMBERSHIP_HEAD)})
    install(state)
    offers = await adapter().discover_in_session(FakePage(state))
    check(len(offers) == 1, "subscriber state unreadable: nothing from Choice (fail safe)")
    humble.logger.removeHandler(cap)


def choice_offer(cid, title, delivery):
    return humble.choice_offers_from_items([choice(cid, title, delivery)], "2026-11")[0]


async def test_choice_claims() -> None:
    print("choice claims")
    reveal = FakeCard("DYSMANTLE", "DYSMANTLE\nKeys expire. Please reveal your key before Jun 1, 2027.", {"REVEAL"})
    revealed = FakeCard("Space Haven", "Space Haven\nAB12C-DE34F-GH56J\nRedeemed on Steam", set())
    linked = FakeCard("The Outer Worlds 2", "The Outer Worlds 2\nA linked Battle.net account is required. "
                      "Key will be redeemed on Battle.net.", {"LINK", "REVEAL"})

    def on_reveal(loc):
        loc.card.text += "\nQWE12-RTY34-UIO56"
        loc.card.buttons.discard("REVEAL")

    state = new_state(cards={c.title: c for c in (reveal, revealed, linked)},
                      visible={"SEARCH"}, on_click={"REVEAL": on_reveal})
    install(state)
    a = adapter()
    page = FakePage(state)
    offers = [choice_offer("dysmantle_choice", "DYSMANTLE", "steam"),
              choice_offer("spacehaven_choice", "Space Haven", "steam"),
              choice_offer("theouterworlds2_choice", "The Outer Worlds 2", "blizzard")]
    results = {o.title: await a.claim(page, o) for o in offers}

    r = results["DYSMANTLE"]
    check(r.outcome == "claimed" and r.key_code == "QWE12-RTY34-UIO56" and r.key_store == "steam",
          f"unrevealed: claimed with its key ({r.outcome}, {r.key_code}, {r.key_store})")
    r = results["Space Haven"]
    check(r.outcome == "already_owned" and r.key_code == "AB12C-DE34F-GH56J",
          f"already revealed: already_owned with the key ({r.outcome}, {r.key_code})")
    r = results["The Outer Worlds 2"]
    check(r.outcome == "not_eligible" and r.reason_code == humble.REASON_LINK,
          f"Battle.net delivery: link_required ({r.outcome}, {r.reason_code})")
    check(state["clicks"] == [("DYSMANTLE", "REVEAL")],
          f"exactly one click, on the unrevealed game only: {state['clicks']}")
    check(state["filled"] == ["DYSMANTLE", "Space Haven", "The Outer Worlds 2"],
          "the library's own name filter narrowed each lookup")

    # Claiming the same game again: now revealed, so no second click.
    again = await a.claim(page, offers[0])
    check(again.outcome == "already_owned" and len(state["clicks"]) == 1,
          "a second run on the same game clicks nothing more")

    # Platform choice: Cancel is pressed, nothing chosen.
    plat = FakeCard("Space Haven", "Space Haven", {"REVEAL"})

    def on_plat(loc):
        state2["visible"].update({"PLATFORM", "CANCEL"})

    state2 = new_state(cards={"Space Haven": plat}, on_click={"REVEAL": on_plat})
    install(state2)
    r = await adapter().claim(FakePage(state2), offers[1])
    check(r.outcome == "not_eligible" and r.reason_code == humble.REASON_PLATFORM,
          f"platform dialog: platform_choice ({r.outcome}, {r.reason_code})")
    check([c[1] for c in state2["clicks"]] == ["REVEAL", "CANCEL"], f"pressed Reveal then Cancel only: {state2['clicks']}")

    # Error dialog after the click: region.
    reg = FakeCard("DYSMANTLE", "DYSMANTLE", {"REVEAL"})
    state3 = new_state(cards={"DYSMANTLE": reg},
                       dialog_text="Not Available in Region\nThis game isn’t available to redeem in your region.")
    state3["on_click"] = {"REVEAL": lambda loc: state3["visible"].add("DIALOG")}
    install(state3)
    r = await adapter().claim(FakePage(state3), offers[0])
    check(r.reason_code == humble.REASON_REGION, f"region error dialog: region_blocked ({r.reason_code})")

    # Locked month: nothing pressed.
    state4 = new_state(cards={"DYSMANTLE": FakeCard("DYSMANTLE", "DYSMANTLE", {"REVEAL"})}, visible={"PAY_EARLY"})
    install(state4)
    r = await adapter().claim(FakePage(state4), offers[0])
    check(r.reason_code == humble.REASON_NOT_UNLOCKED and not state4["clicks"],
          f"Pay to Unlock showing: not_unlocked, nothing pressed ({r.reason_code}, {state4['clicks']})")

    # Not in the library at all.
    state5 = new_state(cards={})
    install(state5)
    r = await adapter().claim(FakePage(state5), offers[0])
    check(r.reason_code == humble.REASON_NOT_IN_LIBRARY, "missing card: not_in_library")

    # A captcha raises CheckoutBlocked, never a click.
    state6 = new_state(cards={"DYSMANTLE": FakeCard("DYSMANTLE", "DYSMANTLE", {"REVEAL"})}, visible={"CHALLENGE"})
    install(state6)
    try:
        await adapter().claim(FakePage(state6), offers[0])
        check(False, "a challenge must raise")
    except humble.CheckoutBlocked as exc:
        check(exc.offer_id == offers[0].external_id and not state6["clicks"],
              "challenge: CheckoutBlocked naming the offer, nothing pressed")


async def test_store_claims() -> None:
    print("store claims")
    offer = humble.free_offers_from_hits(humble.parse_store_hits(page_html(STORE_FLIGHT)))[0]
    offer.extra["account_id"] = 1
    product = offer.url
    html = {product: page_html(PRODUCT_FLIGHT_FREE)}

    state = new_state(html=html, visible={"ADD_TO_CART", "PLACE_ORDER"},
                      body="Your cart\nFree Game €0.00\nPaid Thing €19.99\nSubtotal €19.99\nTotal €19.99")
    install(state)
    r = await adapter().claim(FakePage(state), offer)
    check(r.outcome == "not_eligible" and r.reason_code == humble.REASON_NOT_FREE,
          f"non-zero total: not_eligible not_free ({r.outcome}, {r.reason_code})")
    check(("page", "PLACE_ORDER") not in state["clicks"], f"non-zero total: the order button was never pressed {state['clicks']}")

    state = new_state(html=html, visible={"ADD_TO_CART", "PLACE_ORDER"}, body="Checkout")
    install(state)
    r = await adapter().claim(FakePage(state), offer)
    check(r.reason_code == humble.REASON_NOT_FREE and ("page", "PLACE_ORDER") not in state["clicks"],
          "unreadable total: nothing ordered")

    state = new_state(html=html, visible={"ADD_TO_CART", "OPTIN"})
    install(state)
    r = await adapter().claim(FakePage(state), offer)
    check(r.outcome == "not_eligible" and r.reason_code == humble.REASON_NEWSLETTER and not state["clicks"],
          f"newsletter gate: not_eligible newsletter_optin, nothing clicked ({r.reason_code}, {state['clicks']})")

    state = new_state(html={product: page_html(PRODUCT_FLIGHT_PAID)}, visible={"ADD_TO_CART"})
    install(state)
    r = await adapter().claim(FakePage(state), offer)
    check(r.reason_code == humble.REASON_NOT_FREE and not state["clicks"], "page price 4.99: nothing clicked")

    state = new_state(html=html, visible={"ADD_TO_CART", "PLACE_ORDER", "CHALLENGE"})
    install(state)
    try:
        await adapter().claim(FakePage(state), offer)
        check(False, "a challenge must raise")
    except humble.CheckoutBlocked:
        check(not state["clicks"], "challenge on the product page: CheckoutBlocked, nothing clicked")

    state = new_state(html=html, visible={"ADD_TO_CART", "PLACE_ORDER"},
                      body="I understand this is a recurring charge of $11.99\nTotal $0.00")
    install(state)
    try:
        await adapter().claim(FakePage(state), offer)
        check(False, "a recurring charge must stop the run")
    except NeedsAttention:
        check(("page", "PLACE_ORDER") not in state["clicks"], "recurring charge in the checkout: stopped, no order")

    card = FakeCard("Free Game", "Free Game\nZXC12-VBN34-MLK56", set())
    state = new_state(html=html, visible={"ADD_TO_CART", "PLACE_ORDER"},
                      body="Free Game €0.00\nSubtotal €0.00\nTotal €0.00", cards={"Free Game": card})
    install(state)
    r = await adapter().claim(FakePage(state), offer)
    orders = [c for c in state["clicks"] if c[1] == "PLACE_ORDER"]
    check(r.outcome == "claimed" and r.key_code == "ZXC12-VBN34-MLK56",
          f"zero total: claimed, key read from the library ({r.outcome}, {r.key_code})")
    check(len(orders) == 1, f"zero total: the order button pressed exactly once ({state['clicks']})")
    check(humble.CHECKOUT_URL in state["gotos"], "the checkout page was read before ordering")


async def main() -> int:
    test_parsers()
    await test_discovery()
    await test_choice_claims()
    await test_store_claims()
    print("PASS" if OK else "FAILED")
    return 0 if OK else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
