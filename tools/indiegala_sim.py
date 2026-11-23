"""IndieGala's freebies parser and claim guards, without a browser or network.

The listing fixture below is the live galaFreebies markup of 23 Nov 2026,
two cells verbatim (whitespace aside), plus cells made deliberately wrong to
prove they are refused. The point is CLAUDE.md's warning about adapters
written from reasoning: a 200, a full page and a cheerful empty list. This
pins that the measured markup parses, that every offer carries the product
UUID (never the slug) as its identity, and that anything that is not plainly
a freebie is skipped.

It also pins the claim's fail-safe branches against a fake page: signed out
stops, owned is `already_owned`, a newsletter opt-in or a missing control is
`not_eligible` with its reason code, and a control that talks about money is
never clicked.

    python tools/indiegala_sim.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
os.environ.setdefault("DATA_DIR", "./data")

import app.adapters.indiegala as ig  # noqa: E402
from app.adapters.base import FreeOffer  # noqa: E402

FAILURES: list[str] = []


def check(name: str, got, expected) -> None:
    ok = got == expected
    print(f"  {'ok  ' if ok else 'FAIL'} {name}: {got!r}")
    if not ok:
        FAILURES.append(f"{name}: expected {expected!r}, got {got!r}")


# Two cells exactly as freebies.indiegala.com served them (measured).
MEASURED_CELLS = """
<div class="col-3 products-col"><div class="products-col-inner box-shadow-1 relative"><a class="fit-click" href="https://freebies.indiegala.com/my-darkest-witch" title="Go to My Darkest Witch page"></a><figure class="product-img"><img alt="My Darkest Witch product image" class="display-none async-img-load" data-img-src="https://www.indiegalacdn.com/imgs/devs/freebies/products/9303ce41-bad2-40e2-8b3f-728d85fcb2dc/prodmain/1761983673.png"/></figure><figcaption class="overflow-auto product-title-cont"><div class="left product-title">My Darkest Witch</div><div class="right product-get-btn">get</div></figcaption></div></div>
<div class="col-3 products-col"><div class="products-col-inner box-shadow-1 relative"><a class="fit-click" href="https://freebies.indiegala.com/die-young-prologue" title="Go to Die Young: Prologue page"></a><figure class="product-img"><img alt="Die Young: Prologue product image" class="display-none async-img-load" data-img-src="https://www.indiegalacdn.com/imgs/devs/freebies/products/6ba4b90d-ec12-4343-bd6f-415f0881ebae/prodmain/1574430858.png"/></figure><figcaption class="overflow-auto product-title-cont"><div class="left product-title">Die Young: Prologue</div><div class="right product-get-btn">get</div></figcaption></div></div>
"""

# The same cell with its attributes in another order - the itch.io trap.
SHUFFLED_CELL = """
<div class="col-3 products-col"><div class="relative products-col-inner box-shadow-1"><a title="Go to Leaper page" href="https://freebies.indiegala.com/leaper" class="fit-click"></a><figure class="product-img"><img data-img-src="https://www.indiegalacdn.com/imgs/devs/freebies/products/a1db8f5b-f9c3-4004-84fc-ce90d0bb5872/prodmain/1759920499.png" class="display-none async-img-load" alt="Leaper product image"/></figure><figcaption class="overflow-auto product-title-cont"><div class="left product-title">Leaper</div><div class="right product-get-btn">get</div></figcaption></div></div>
"""

# Made-up cells that must be refused.
PRICED_CELL = """
<div class="col-3 products-col"><div class="products-col-inner box-shadow-1 relative"><a class="fit-click" href="https://freebies.indiegala.com/paid-thing" title="Go to Paid page"></a><figure class="product-img"><img data-img-src="https://www.indiegalacdn.com/imgs/devs/freebies/products/11111111-2222-3333-4444-555555555555/prodmain/1.png"/></figure><figcaption class="overflow-auto product-title-cont"><div class="left product-title">Paid Thing</div><div class="right product-get-btn">$4.99</div></figcaption></div></div>
"""
NO_UUID_CELL = """
<div class="col-3 products-col"><div class="products-col-inner box-shadow-1 relative"><a class="fit-click" href="https://freebies.indiegala.com/no-uuid" title="Go to No UUID page"></a><figure class="product-img"><img data-img-src="https://www.indiegalacdn.com/imgs/devs/freebies/avatar/1544691623.png"/></figure><figcaption class="overflow-auto product-title-cont"><div class="left product-title">No UUID</div><div class="right product-get-btn">get</div></figcaption></div></div>
"""
OFF_HOST_CELL = """
<div class="col-3 products-col"><div class="products-col-inner box-shadow-1 relative"><a class="fit-click" href="https://www.indiegala.com/store/game/something/123" title="Go to Store page"></a><figure class="product-img"><img data-img-src="https://www.indiegalacdn.com/imgs/devs/freebies/products/66666666-2222-3333-4444-555555555555/prodmain/1.png"/></figure><figcaption class="overflow-auto product-title-cont"><div class="left product-title">Store Game</div><div class="right product-get-btn">get</div></figcaption></div></div>
"""


def page(*cells: str) -> str:
    return (
        "<html><head><script>\n\tconst userIsLogged = false;\n</script></head><body>"
        '<h2 class="products-title section-title">Games</h2><div class="row products-row">'
        + "".join(cells)
        + "</div><footer>Terms and conditions</footer></body></html>"
    )


def listing_checks() -> None:
    print("the freebies listing")
    entries = ig._parse_listing(page(MEASURED_CELLS))
    check("measured markup: two freebies", len(entries), 2)
    check(
        "ids are the product UUIDs from the cover path",
        [e["uuid"] for e in entries],
        ["9303ce41-bad2-40e2-8b3f-728d85fcb2dc", "6ba4b90d-ec12-4343-bd6f-415f0881ebae"],
    )
    check(
        "titles and urls",
        [(e["title"], e["url"]) for e in entries],
        [
            ("My Darkest Witch", "https://freebies.indiegala.com/my-darkest-witch"),
            ("Die Young: Prologue", "https://freebies.indiegala.com/die-young-prologue"),
        ],
    )
    check("slug kept as extra, not identity", entries[0]["slug"], "my-darkest-witch")
    check(
        "attribute order does not matter",
        [e["title"] for e in ig._parse_listing(page(SHUFFLED_CELL))],
        ["Leaper"],
    )
    check("a priced cell is refused", ig._parse_listing(page(PRICED_CELL)), [])
    check("a cell with no product UUID is refused", ig._parse_listing(page(NO_UUID_CELL)), [])
    check("a cell linking off the freebies host is refused", ig._parse_listing(page(OFF_HOST_CELL)), [])
    check(
        "a duplicate cell is one offer",
        len(ig._parse_listing(page(MEASURED_CELLS, MEASURED_CELLS))),
        2,
    )
    check("an empty page is empty", ig._parse_listing(page()), [])
    check(
        "a Cloudflare interstitial is recognised",
        ig._looks_like_challenge("<html><head><title>Just a moment...</title>"),
        True,
    )
    check("the real page is not a challenge", ig._looks_like_challenge(page(MEASURED_CELLS)), False)


def flag_checks() -> None:
    print("the page's own signed-in flag")
    check("measured signed out", ig.read_logged_flag("\tconst userIsLogged = false;\n"), False)
    check("signed in", ig.read_logged_flag("const userIsLogged = true;"), True)
    check(
        "a later test of the flag is not the declaration",
        ig.read_logged_flag("if ( !userIsLogged ){ return false; }"),
        None,
    )
    check("absent is unknown", ig.read_logged_flag("<html></html>"), None)


def guard_checks() -> None:
    print("the money guard")
    on = "https://freebies.indiegala.com/my-darkest-witch"
    check("ADD TO LIBRARY with href #", ig._is_free_control("ADD TO LIBRARY", "#", on), True)
    check("a library link on www", ig._is_free_control("Add to library", "https://www.indiegala.com/library", on), True)
    check("Buy now", ig._is_free_control("Buy now", "#", on), False)
    check("a price in the label", ig._is_free_control("Get it for $1.99", "#", on), False)
    check("a cart link", ig._is_free_control("Add to library", "/cart/add/1", on), False)
    check("a store link", ig._is_free_control("Get", "https://www.indiegala.com/store/game/x/1", on), False)
    check("an off-site link", ig._is_free_control("Download", "https://example.com/x", on), False)
    check("an unrelated label", ig._is_free_control("Comments", "#", on), False)
    check("not on the freebies host", ig._is_free_control("Add to library", "#", "https://www.indiegala.com/store"), False)
    print("keys")
    check(
        "a Steam key with its store",
        ig.extract_key("Your Steam key: ABCDE-12345-FGHIJ"),
        ("ABCDE-12345-FGHIJ", "steam"),
    )
    check("a key with no store named", ig.extract_key("Key: ABCDE-12345-FGHIJ-KLMNO"), ("ABCDE-12345-FGHIJ-KLMNO", None))
    check("no key", ig.extract_key("ADD TO LIBRARY"), None)


class FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        pass


class FakeClient:
    text = ""

    def __init__(self, *a, **kw) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a) -> bool:
        return False

    async def get(self, url, params=None) -> FakeResponse:
        return FakeResponse(FakeClient.text)


async def discovery_checks() -> None:
    print("discovery, end to end with a fake client")
    real = ig.httpx.AsyncClient
    ig.httpx.AsyncClient = FakeClient
    try:
        FakeClient.text = page(MEASURED_CELLS, PRICED_CELL)
        offers = await ig.IndieGalaAdapter().list_free_offers()
        check("only the freebies", [o.title for o in offers], ["My Darkest Witch", "Die Young: Prologue"])
        check("external_id is the UUID", offers[0].external_id, "9303ce41-bad2-40e2-8b3f-728d85fcb2dc")
        check("no end date is invented", offers[0].ends_at, None)

        many = "".join(
            MEASURED_CELLS.split("\n")[1]
            .replace("9303ce41-bad2-40e2-8b3f-728d85fcb2dc", f"9303ce41-bad2-40e2-8b3f-{i:012d}")
            .replace("my-darkest-witch", f"witch-{i}")
            for i in range(ig.MAX_OFFERS + 3)
        )
        FakeClient.text = page(many)
        offers = await ig.IndieGalaAdapter().list_free_offers()
        check("capped at MAX_OFFERS", len(offers), ig.MAX_OFFERS)

        FakeClient.text = "<html><head><title>Just a moment...</title></head></html>"
        check("a challenge reports nothing", await ig.IndieGalaAdapter().list_free_offers(), [])
    finally:
        ig.httpx.AsyncClient = real


class FakeLoc:
    def __init__(self, page: "FakePage", sel: str, visible: bool) -> None:
        self.page, self.sel, self._visible = page, sel, visible

    @property
    def first(self):
        return self

    async def is_visible(self) -> bool:
        return self._visible

    async def inner_text(self) -> str:
        return self.page.texts.get(self.sel, "")

    async def get_attribute(self, name: str):
        return self.page.hrefs.get(self.sel) if name == "href" else None

    async def click(self, **kw) -> None:
        self.page.clicks.append(self.sel)


class FakeReply:
    def __init__(self, status) -> None:
        self.status = status

    async def json(self):
        if isinstance(self.status, Exception):
            raise self.status
        return {"status": self.status}


class FakeExpect:
    """`page.expect_response`: the add-to-library POST's reply, or a timeout."""

    def __init__(self, page: "FakePage", predicate) -> None:
        self.page, self.predicate = page, predicate

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        if exc[0] is not None:
            return False
        if self.page.reply is None:
            raise ig.PlaywrightTimeout("no reply")
        url = "https://freebies.indiegala.com" + ig.ADD_TO_LIBRARY_REPLY + "uuid/slug/freebies"
        assert self.predicate(type("R", (), {"url": url})())
        return False

    @property
    def value(self):
        async def _v():
            return FakeReply(self.page.reply)
        return _v()


class FakePage:
    def __init__(self, visible: set[str], texts=None, hrefs=None, url="https://freebies.indiegala.com/x", reply=None) -> None:
        self.visible = visible
        self.texts = texts or {}
        self.hrefs = hrefs or {}
        self.url = url
        self.reply = reply
        self.clicks: list[str] = []

    def expect_response(self, predicate, timeout=None):
        return FakeExpect(self, predicate)

    def locator(self, sel: str) -> FakeLoc:
        base = sel.split(" >> visible=true")[0]
        return FakeLoc(self, base, base in self.visible)

    async def goto(self, url: str, **kw):
        return None

    async def title(self) -> str:
        return "Freebie - galaFreebies | Indiegala Showcase"

    async def content(self) -> str:
        return ""

    async def wait_for_load_state(self, *a, **kw) -> None:
        pass


async def claim_checks() -> None:
    print("the claim's fail-safe branches")
    adapter = ig.IndieGalaAdapter()

    async def no_shot(*a, **kw):
        return None

    adapter._shot = no_shot
    offer = FreeOffer(external_id="u", title="Freebie", url="https://freebies.indiegala.com/x", extra={})
    section = ig.DOWNLOAD_SECTION[0]
    control = ig.ADD_TO_LIBRARY[0]

    async def run(fake: FakePage):
        try:
            result = await adapter.claim(fake, offer)
            return (result.outcome, result.reason_code)
        except ig.CheckoutBlocked:
            return "checkout_blocked"
        except ig.NeedsAttention:
            return "needs_attention"

    check(
        "signed out (the -login button) stops",
        await run(FakePage({section, ig.SIGNED_OUT[1]})),
        "needs_attention",
    )
    check("a challenge goes to the un-driven window", await run(FakePage({ig.CHALLENGE[0]})), "checkout_blocked")
    # The measured page: a button, and help text linking to the library. That
    # link is NOT ownership (it fooled the first version into "owned" for all).
    help_link = '.developer-product-download a[href*="/library"]'
    for status, want in (("ok", ("claimed", None)), ("added", ("already_owned", None))):
        fake = FakePage({section, control, help_link}, texts={control: "ADD TO LIBRARY"}, reply=status)
        check(f"reply {status!r} -> {want[0]}", await run(fake), want)
        check(f"reply {status!r}: one click", fake.clicks, [control])
    fake = FakePage({section, control}, texts={control: "ADD TO LIBRARY"}, reply="login")
    check("reply 'login' is signed out", await run(fake) == "needs_attention", True)
    fake = FakePage({section, control}, texts={control: "ADD TO LIBRARY"}, reply="error")
    check("any other reply stops", await run(fake), "needs_attention")
    check("the help text's library link is not ownership", help_link in ig.OWNED, False)
    check("the measured button is the first control", ig.ADD_TO_LIBRARY[0].endswith('button[onclick*="addToLibrary"]'), True)
    check("verdicts", [ig.reply_verdict(x) for x in ("ok", "added", "login", "", "nope")],
          ["claimed", "already_owned", "signed_out", "error", "error"])
    check("no Download section stops", await run(FakePage(set())), "needs_attention")
    fake = FakePage({section, ig.NEWSLETTER[0], control}, texts={control: "ADD TO LIBRARY"})
    check("a newsletter opt-in is not_eligible", await run(fake), ("not_eligible", ig.REASON_NEWSLETTER))
    check("and nothing was clicked", fake.clicks, [])
    check(
        "an account level is not_eligible",
        await run(FakePage({section, ig.ACCOUNT_LEVEL[0]})),
        ("not_eligible", ig.REASON_ACCOUNT_LEVEL),
    )
    check(
        "no claim control is not_eligible",
        await run(FakePage({section})),
        ("not_eligible", ig.REASON_NO_CONTROL),
    )
    fake = FakePage({section, control}, texts={control: "BUY NOW $4.99"})
    check("a money-worded control is refused", await run(fake), "needs_attention")
    check("and nothing was clicked", fake.clicks, [])


async def main() -> int:
    listing_checks()
    flag_checks()
    guard_checks()
    await discovery_checks()
    await claim_checks()
    print()
    if FAILURES:
        for failure in FAILURES:
            print("FAILED:", failure)
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
