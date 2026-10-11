"""Steam's free-to-keep guards, without a browser or the network.

Everything here was written against a live promotion (25 Aug 2026, the
"Microsoft Flight Simulator Suite: Themes Reimagined" giveaway) after three of
these went wrong on a real run, so the point is to stop them going wrong
again quietly.

1. **Which package is the free one.** An app can list a free promotional sub
   beside a paid one. Steam marks the free one `is_free_license` at 0 cents.
   `can_get_free_license` is deliberately not used: it reads "0" for everybody
   when signed out, so it answers a question about the viewer, not the package.

2. **What counts as an add-on.** The soundtrack promotion answers
   `type: "music"`, not `"dlc"`. Keying the ledger's `kind` off `type` recorded
   it as a game, so the runner's base-game prerequisite never ran and a claim
   was attempted for an add-on to a game the account does not own. Anything
   carrying a `fullgame` is an add-on to that game, whatever Steam calls it.

3. **The base game's verdict.** `inspect_base_game` must answer `owned=True`,
   or `owned=False` with a price, or `None` everywhere when it could not tell -
   never `False` for a page it failed to read. The runner treats those two
   differently and only one of them is a finding.

4. **A cart-shaped button that is a free grant.** Pony Island (11 Oct 2026)
   rendered its free block's "Add to Account" as `javascript:addToCart(
   1857153)` - but the form that names, `add_to_cart_1857153`, posts to
   `/freelicense/addfreelicense/`, and Steam's own `addToCart` submits such a
   form without touching the cart. That is free and must be clicked; a
   cart-action whose form posts to `/cart/`, or has no form, must still be
   refused.

    python tools/steam_sim.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
os.environ.setdefault("DATA_DIR", "./data")

import app.adapters.steam as steam  # noqa: E402
from app.adapters.base import FreeOffer  # noqa: E402

FAILURES: list[str] = []


def check(name: str, got, expected) -> None:
    ok = got == expected
    print(f"  {'ok  ' if ok else 'FAIL'} {name}: {got!r}")
    if not ok:
        FAILURES.append(f"{name}: expected {expected!r}, got {got!r}")


# The measured shape, both subs, exactly as appdetails returned them.
MEASURED = {
    "package_groups": [
        {
            "subs": [
                {
                    "packageid": 1789618,
                    "option_text": "Limited Free Promotional Package - Aug 2026",
                    "can_get_free_license": "0",
                    "is_free_license": True,
                    "price_in_cents_with_discount": 0,
                },
                {
                    "packageid": 1735686,
                    "option_text": "Microsoft Flight Simulator 2024 Soundtrack",
                    "can_get_free_license": "0",
                    "is_free_license": False,
                    "price_in_cents_with_discount": 999,
                },
            ]
        }
    ]
}

PAID_SUB = MEASURED["package_groups"][0]["subs"][1]


def package_checks() -> None:
    print("the free promotional package")
    check(
        "picks the free sub, not the priced one",
        steam._free_package_id(MEASURED),
        1789618,
    )
    check(
        "no free sub at all",
        steam._free_package_id({"package_groups": [{"subs": [PAID_SUB]}]}),
        None,
    )
    check(
        "flagged free but priced is not free",
        steam._free_package_id(
            {
                "package_groups": [
                    {
                        "subs": [
                            {
                                "packageid": 9,
                                "is_free_license": True,
                                "price_in_cents_with_discount": 500,
                            }
                        ]
                    }
                ]
            }
        ),
        None,
    )
    check("nothing to read", steam._free_package_id({}), None)


def kind_of(data: dict) -> str:
    """The expression discovery uses, kept in step with steam.py by hand."""
    return (
        "dlc"
        if (data.get("fullgame") or {}).get("appid") or data.get("type") == "dlc"
        else "game"
    )


def kind_checks() -> None:
    print("what counts as an add-on")
    check(
        "music with a fullgame is an add-on",
        kind_of({"type": "music", "fullgame": {"appid": "2537590"}}),
        "dlc",
    )
    check("dlc with no fullgame is still one", kind_of({"type": "dlc"}), "dlc")
    check("a plain game is a game", kind_of({"type": "game"}), "game")
    check(
        "an empty fullgame does not make one",
        kind_of({"type": "game", "fullgame": {}}),
        "game",
    )


class FakeLoc:
    def __init__(self, visible: bool) -> None:
        self._visible = visible

    @property
    def first(self):
        # `_first_visible` takes `.first`; without it the AttributeError is
        # swallowed as "selector did not resolve" and every check reads as
        # "not visible" - which is how this fake first reported an owned game
        # as unowned.
        return self

    async def is_visible(self) -> bool:
        return self._visible


class FakePage:
    """Only what `inspect_base_game` touches: goto, and visible selectors."""

    def __init__(self, visible: set[str]) -> None:
        self.visible = visible
        self.url = ""

    def locator(self, sel: str) -> FakeLoc:
        # Playwright resolves "<sel> >> visible=true" to the visible matches of
        # <sel>; this fake models visibility itself, so it strips the suffix.
        return FakeLoc(sel.split(" >> visible=true")[0] in self.visible)

    async def goto(self, url: str, **kw):
        self.url = url
        return None

    async def wait_for_timeout(self, ms: int) -> None:
        pass


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._payload


class FakeClient:
    """Stands in for httpx, so the sim never touches the network."""

    payload: dict = {}

    def __init__(self, *a, **kw) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a) -> bool:
        return False

    async def get(self, url, params=None) -> FakeResponse:
        return FakeResponse(FakeClient.payload)


async def base_game_checks() -> None:
    print("the base game's verdict")
    adapter = steam.SteamAdapter()
    offer = FreeOffer(
        external_id="5004030",
        title="Themes Reimagined",
        url="u",
        kind="dlc",
        extra={
            "base_appid": "2537590",
            "base_title": "Microsoft Flight Simulator 2024",
        },
    )
    real_client, real_goto = steam.httpx.AsyncClient, steam._goto
    steam.httpx.AsyncClient = FakeClient

    async def fake_goto(page, url, **kw):
        await page.goto(url)

    steam._goto = fake_goto
    try:
        FakeClient.payload = {}
        base = await adapter.inspect_base_game(FakePage({steam.OWNED[0]}), offer)
        check(
            "owned base game",
            (base.owned, base.title),
            (True, "Microsoft Flight Simulator 2024"),
        )

        FakeClient.payload = {
            "2537590": {
                "data": {
                    "name": "Microsoft Flight Simulator 2024",
                    "is_free": False,
                    "price_overview": {"final": 6999, "final_formatted": "$69.99"},
                }
            }
        }
        base = await adapter.inspect_base_game(FakePage(set()), offer)
        check(
            "unowned and paid, with the price for the ledger",
            (base.owned, base.free, base.price_note),
            (False, False, "$69.99"),
        )

        FakeClient.payload = {
            "2537590": {"data": {"name": "Free Game", "is_free": True}}
        }
        base = await adapter.inspect_base_game(FakePage(set()), offer)
        check(
            "unowned but free to play",
            (base.owned, base.free),
            (False, True),
        )

        FakeClient.payload = {}
        base = await adapter.inspect_base_game(
            FakePage({steam.SIGNED_OUT[0]}), offer
        )
        check(
            "signed out is not 'does not own it'",
            (base.owned, base.free),
            (None, None),
        )

        plain = FreeOffer(
            external_id="1", title="Game", url="u", kind="game", extra={}
        )
        check(
            "a plain game has no base game",
            await adapter.inspect_base_game(FakePage(set()), plain),
            None,
        )
    finally:
        steam.httpx.AsyncClient, steam._goto = real_client, real_goto


class FakeButton:
    def __init__(self, href: str) -> None:
        self.href = href

    async def get_attribute(self, name: str):
        return self.href if name == "href" else None


class FakeForm:
    def __init__(self, action: str | None) -> None:
        self.action = action

    async def count(self) -> int:
        return 0 if self.action is None else 1

    async def get_attribute(self, name: str):
        return self.action


class FormPage:
    """A page holding one `add_to_cart_<subid>` form, or none."""

    def __init__(self, subid: str, action: str | None) -> None:
        self.subid, self.action = subid, action

    def locator(self, sel: str) -> FakeForm:
        named = f'form[name="add_to_cart_{self.subid}"]'
        return FakeForm(self.action if sel == named else None)


async def cart_checks() -> None:
    print("A cart-shaped button and the form behind it:")
    adapter = steam.SteamAdapter()
    offer = FreeOffer(external_id="405640", title="Pony Island", url="u", kind="game", extra={})

    async def no_shot(*a, **kw):
        return None

    adapter._shot = no_shot
    href = "javascript:addToCart( 1857153);"

    async def verdict(page) -> str:
        try:
            await adapter._assert_free_click(page, offer, FakeButton(href))
            return "click"
        except steam.NeedsAttention:
            return "refuse"

    check(
        "form posts to /freelicense/ (measured)",
        await verdict(FormPage("1857153", "https://store.steampowered.com/freelicense/addfreelicense/")),
        "click",
    )
    check(
        "form posts to /cart/",
        await verdict(FormPage("1857153", "https://store.steampowered.com/cart/")),
        "refuse",
    )
    check("no form at all", await verdict(FormPage("1857153", None)), "refuse")
    check(
        "a different sub's free form",
        await verdict(FormPage("999", "https://store.steampowered.com/freelicense/addfreelicense/")),
        "refuse",
    )


async def main() -> int:
    package_checks()
    kind_checks()
    await base_game_checks()
    await cart_checks()
    print()
    if FAILURES:
        for failure in FAILURES:
            print("FAILED:", failure)
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
