"""Signing an account back in by itself, when a check finds it signed out.

Asked for by the person whose accounts these are (23 Nov 2026): "make all
the accounts and sites work as far as possible without any of my input, even
for login". It uses the sign-in details they stored on the account, and the
rules that keep it from becoming what bot detection looks for are these:

* **Only when a check has just read the account as signed out**, never on a
  schedule of its own, and at most once per `COOLDOWN_H` per account.
* **It reads the form; it does not type blind.** The assisted sign-in types
  into a window with nothing attached, so it cannot tell GOG's two forms
  apart and an Enter in the wrong place is a failed login. Here Chrome has a
  DevTools port (the warm session's launch, which Cloudflare was measured to
  let through) and no Playwright, so the page can be asked which fields are
  actually on it before anything is typed.
* **It stops at anything a person must answer**: a visible captcha (an
  invisible reCAPTCHA badge is not one), a code it cannot produce (emailed or
  from a phone app; a stored TOTP secret it can), or an error from the store.
  It never solves a captcha.
* **A password the store refused is not tried again** until a person has
  signed in (`Outcome.pause`). Neither is a sign-in that ends at an emailed
  code: each attempt would send another email.
* **"Remember me" is ticked when the form has one**; nothing else is.

It returns what happened. Whether the account is now signed in is the
adapter's `health` to say, read afterwards by `runner.check_session` in the
ordinary driven browser - nothing here decides that.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
from dataclasses import dataclass
from pathlib import Path

from .browser import _page_targets, is_challenge_title, manager

logger = logging.getLogger("trove.autosignin")

COOLDOWN_H = 12
# How long one attempt may take in all, and how long the page gets after a
# step before it is read again.
ATTEMPT_S = 150.0
STEP_WAIT_S = 3.0
# A page with no form on it is read this many times before giving up: login
# pages render their form client-side and some take a while.
EMPTY_READS_BEFORE_GIVING_UP = 6


@dataclass
class Outcome:
    """What one attempt came to.

    `submitted` means the details went in and nothing visible objected; the
    session check afterwards is the verdict. `pause` means do not try again
    until a person has signed in.
    """

    submitted: bool
    sentence: str
    pause: bool = False


# Reads the page and tags what it finds with `data-trove-auto`, so the next
# call can reach the same element without re-deriving it. Returns JSON.
_INSPECT_JS = r"""
(() => {
  const visible = (e) => {
    const r = e.getBoundingClientRect(), s = getComputedStyle(e);
    return r.width > 2 && r.height > 2 && s.visibility !== 'hidden' && s.display !== 'none' && Number(s.opacity) > 0.05;
  };
  const words = (e) => [e.name, e.id, e.placeholder, e.autocomplete, e.getAttribute('aria-label'),
                        e.labels && [...e.labels].map(l => l.innerText).join(' ')].join(' ').toLowerCase();
  document.querySelectorAll('[data-trove-auto]').forEach(e => e.removeAttribute('data-trove-auto'));
  const inputs = [...document.querySelectorAll('input')].filter(e => visible(e) && !e.disabled && !e.readOnly);
  const textish = inputs.filter(e => ['text', 'email', 'tel', 'number', ''].includes(e.type));
  const isCode = (e) => /one-time-code/.test(e.autocomplete || '') ||
                        /\b(otp|totp|2fa|mfa|two.?factor|security code|verification|authenticat|passcode)\b|code/.test(words(e)) && !/promo|coupon|zip|post/.test(words(e));
  const isSearch = (e) => /search|query|\bq\b/.test(words(e));
  // A newsletter box is an email field too. Typing into one and pressing Enter
  // subscribes the person to something, so anything in a footer or near the
  // word is not a sign-in field.
  const isNewsletter = (e) => !!e.closest('footer') ||
    /newsletter|subscribe|sign me up|deals in your inbox/i.test(e.closest('form, section')?.innerText || '');
  const isUser = (e) => e.type === 'email' || /email|e-mail|user|login|account|identifier|sign.?in/.test(words(e));
  const password = inputs.filter(e => e.type === 'password');
  const codes = textish.filter(e => isCode(e) && !isSearch(e));
  let user = textish.filter(e => !isCode(e) && !isSearch(e) && !isNewsletter(e) && isUser(e));
  if (!user.length && password.length) {
    // A form with a password and one unlabelled text field: that field is the user name.
    const form = password[0].form;
    user = textish.filter(e => !isSearch(e) && !isCode(e) && !isNewsletter(e) && (!form || e.form === form));
  }
  const tag = (e, what) => { if (e) e.setAttribute('data-trove-auto', what); return e ? {empty: !e.value} : null; };
  const frames = [...document.querySelectorAll('iframe')].filter(visible);
  const captcha = frames.some(f => /hcaptcha|recaptcha|arkoselabs|funcaptcha|challenges\.cloudflare\.com|captcha/i.test(f.src || '')
                                  && !/size=invisible/.test(f.src || '') && f.getBoundingClientRect().height > 50)
                  || [...document.querySelectorAll('.h-captcha, .g-recaptcha, [data-sitekey]')].some(e => visible(e) && e.getBoundingClientRect().height > 50);
  const loginFrame = frames.map(f => f.src || '').find(src => /^https:\/\/[^/]*(login|auth|signin|account|id)\./i.test(src)) || null;
  const remember = [...document.querySelectorAll('input[type=checkbox]')].find(c => {
    const text = ((c.labels && [...c.labels].map(l => l.innerText).join(' ')) || c.closest('label')?.innerText || c.parentElement?.innerText || '').toLowerCase();
    return !c.checked && /remember|keep me|stay signed|trust this/.test(text) && !/newsletter|offers|marketing|deals|subscribe/.test(text);
  });
  if (remember) remember.setAttribute('data-trove-auto', 'remember');
  // The form's own submit button, for whichever field is being filled. Never
  // a social sign-in ("Continue with Google"), never show/hide or "forgot".
  const field = codes[0] || password[0] || user[0];
  const notThis = /google|apple|facebook|steam|discord|xbox|twitch|microsoft|paypal|amazon|show|hide|forgot|reset|cancel|back|create|register/i;
  const label = (b) => (b.innerText || b.value || b.getAttribute('aria-label') || '').trim();
  let submit = null;
  if (field) {
    const scope = field.form || field.closest('div[class], section, main') || document.body;
    const buttons = [...scope.querySelectorAll('button, input[type=submit], [role=button]')].filter(b => visible(b) && !b.disabled && !notThis.test(label(b)));
    submit = buttons.find(b => b.type === 'submit' && field.form && b.form === field.form)
          || buttons.find(b => /^(sign ?in|log ?in|login|continue|next|submit|verify|confirm)\b/i.test(label(b)))
          || null;
  }
  if (submit) submit.setAttribute('data-trove-auto', 'submit');
  // A second-step chooser (Humble: "Send code to <email>" or "Use your
  // Authenticator App (TOTP)"). Only the authenticator choice is ever tagged
  // to press; an emailed code is something a person reads.
  const clickables = [...document.querySelectorAll('button, [role=button], a')].filter(visible);
  const authApp = !field && clickables.find(b => /authenticator|totp|authentication app/i.test(label(b)));
  if (authApp) authApp.setAttribute('data-trove-auto', 'authapp');
  const sendCode = !field && clickables.some(b => /send (a |the )?code|email me|text me|send (an )?email/i.test(label(b)));
  const alerts = [...document.querySelectorAll('[role=alert], [aria-live=assertive], [class*=error i], [class*=alert i], [data-testid*=message i], [class*=message i]')]
    .filter(visible).map(e => e.innerText.trim()).filter(t => t && t.length < 300);
  const error = alerts.find(t => /incorrect|invalid|wrong|not (be )?(found|recogni[sz]ed)|doesn.t match|did not match|too many|try again later|locked|couldn.t sign|unable to sign/i.test(t)) || null;
  return JSON.stringify({
    url: location.href, title: document.title,
    user: tag(user[0], 'user'), password: tag(password[0], 'password'), code: tag(codes[0], 'code'),
    captcha, loginFrame, remember: !!remember, error, authApp: !!authApp, sendCode, submit: submit ? label(submit).slice(0, 40) : null,
  });
})()
"""


class _Cdp:
    """A bare DevTools connection to one page: commands in, results out."""

    def __init__(self, ws) -> None:
        self.ws = ws
        self.next_id = 0

    async def call(self, method: str, params: dict | None = None) -> dict:
        self.next_id += 1
        my_id = self.next_id
        await self.ws.send(json.dumps({"id": my_id, "method": method, "params": params or {}}))
        while True:
            message = json.loads(await asyncio.wait_for(self.ws.recv(), timeout=30))
            if message.get("id") == my_id:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error'].get('message')}")
                return message.get("result") or {}

    async def evaluate(self, expression: str):
        result = await self.call(
            "Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": True}
        )
        return (result.get("result") or {}).get("value")

    async def inspect(self) -> dict:
        try:
            return json.loads(await self.evaluate(_INSPECT_JS) or "{}")
        except (json.JSONDecodeError, TypeError):
            return {}

    async def type_into(self, what: str, text: str) -> None:
        """Focus the tagged field, clear it, and type `text` a key at a time."""
        await self.evaluate(
            f"(() => {{ const e = document.querySelector('[data-trove-auto={what}]');"
            " if (e) { e.scrollIntoView({block: 'center'}); e.focus(); e.select && e.select(); } })()"
        )
        await asyncio.sleep(random.uniform(0.3, 0.6))
        for char in text:
            await self.call("Input.insertText", {"text": char})
            await asyncio.sleep(random.uniform(0.04, 0.12))

    async def press_enter(self) -> None:
        # A beat first: an Enter sent straight after the last typed character
        # was measured to submit nothing at all.
        await asyncio.sleep(random.uniform(0.6, 1.0))
        key = {"key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13}
        # rawKeyDown + char, not keyDown with text: measured on a plain form,
        # the latter fills nothing in and submits nothing.
        await self.call("Input.dispatchKeyEvent", {"type": "rawKeyDown", **key})
        await self.call("Input.dispatchKeyEvent", {"type": "char", "text": "\r", "unmodifiedText": "\r", **key})
        await self.call("Input.dispatchKeyEvent", {"type": "keyUp", **key})

    async def submit(self, state: dict) -> None:
        """Press the form's own button if one was found, else Enter."""
        await asyncio.sleep(random.uniform(0.4, 0.8))
        await self.call("Page.bringToFront")
        if state.get("submit") is not None and await self.evaluate(
            "!!document.querySelector('[data-trove-auto=submit]')"
        ):
            await self.click_tagged("submit")
        else:
            await self.press_enter()

    async def click_tagged(self, what: str) -> None:
        """A real (trusted) mouse click in the middle of the tagged element."""
        box = await self.evaluate(
            f"(() => {{ const e = document.querySelector('[data-trove-auto={what}]'); if (!e) return null;"
            " e.scrollIntoView({block: 'center'}); const r = e.getBoundingClientRect();"
            " return [r.left + r.width / 2, r.top + r.height / 2]; })()"
        )
        if not box:
            return
        x, y = box
        await self.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y, "buttons": 0})
        for kind in ("mousePressed", "mouseReleased"):
            await self.call(
                "Input.dispatchMouseEvent",
                {"type": kind, "x": x, "y": y, "button": "left", "buttons": 1 if kind == "mousePressed" else 0,
                 "clickCount": 1},
            )
            await asyncio.sleep(0.08)

    async def navigate(self, url: str) -> None:
        await self.call("Page.navigate", {"url": url})


async def _wait_for_page(port: int, seconds: float = 30.0) -> None:
    """Until the tab has a real title that is not a security check."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + seconds
    while loop.time() < deadline:
        await asyncio.sleep(1.5)
        targets = _page_targets(port)
        title = targets[0].get("title", "") if targets else ""
        url = targets[0].get("url", "") if targets else ""
        if url.startswith("http") and title and not title.startswith("http") and not is_challenge_title(title):
            return


async def attempt(
    account_id: int,
    profile_path: Path,
    url: str,
    email: str,
    password: str,
    totp=None,
    store_name: str = "the store",
    landing_url: str | None = None,
) -> Outcome:
    """One automatic sign-in on this profile, starting at `url`.

    `totp` is a zero-argument callable producing the current code, or None.
    Holds the profile's lock for the whole attempt.
    """
    import websockets

    async with manager.bare_session(account_id, profile_path, holder="an automatic sign-in") as port:
        target = _page_targets(port)[0]
        async with websockets.connect(target["webSocketDebuggerUrl"], max_size=None) as ws:
            cdp = _Cdp(ws)
            # A window Chrome thinks is hidden (behind another, on a desktop)
            # drops every mouse and key event sent to it - measured: the page
            # read `visibilityState: hidden` and a click on its own button did
            # nothing at all. Typing still worked, which made it look like a
            # problem with the keys.
            await cdp.call("Page.bringToFront")
            await cdp.navigate(url)
            await _wait_for_page(port)
            outcome = await _drive(cdp, port, email, password, totp, store_name)
            if outcome.submitted and landing_url:
                # Finish on the store's own page before the browser closes. A
                # store whose login lives on another host (Humble's
                # auth.humblebundle.com) sets the store's session on the way
                # back; closing mid-redirect left the login host signed in and
                # the store signed out (measured, 23 Nov 2026).
                await cdp.navigate(landing_url)
                await _wait_for_page(port)
                await asyncio.sleep(STEP_WAIT_S * 2)
            return outcome


async def _drive(cdp: _Cdp, port: int, email: str, password: str, totp, store_name: str) -> Outcome:
    loop = asyncio.get_event_loop()
    deadline = loop.time() + ATTEMPT_S
    typed_user = typed_password = typed_code = followed_frame = chose_app = False
    code_judged_at = 0.0
    empty_reads = 0
    gone_reads = 0
    while loop.time() < deadline:
        state = await cdp.inspect()
        title = state.get("title") or ""
        logger.info(
            "Automatic sign-in on %s: %s | user=%s password=%s code=%s captcha=%s error=%r",
            store_name, (state.get("url") or "")[:90], state.get("user"), state.get("password"),
            state.get("code"), state.get("captcha"), state.get("error"),
        )
        if is_challenge_title(title):
            await asyncio.sleep(STEP_WAIT_S)
            continue
        if state.get("error"):
            # Said once, and never typed again: a refused password tried twice
            # is how an account gets locked.
            return Outcome(False, f"{store_name} answered the sign-in with: \"{state['error']}\"", pause=typed_password)
        if state.get("captcha"):
            return Outcome(False, f"{store_name} put a captcha on the sign-in, and Trove never answers one.")

        user, pw, code = state.get("user"), state.get("password"), state.get("code")
        if code and code.get("empty"):
            if typed_code:
                # An empty code field seconds after the code went in is as
                # likely the page still working as a refusal; a refusal comes
                # with an error, caught above. Give it until `code_judged_at`.
                if loop.time() < code_judged_at:
                    await asyncio.sleep(STEP_WAIT_S)
                    continue
                return Outcome(False, f"{store_name} did not accept the authenticator code.", pause=True)
            if totp is None:
                return Outcome(
                    False,
                    f"{store_name} asked for a security code (sent by email or shown in an app), "
                    "which Trove cannot read.",
                    pause=True,
                )
            await cdp.type_into("code", totp())
            await cdp.submit(state)
            typed_code = True
            code_judged_at = loop.time() + 15
        elif pw and pw.get("empty"):
            if typed_password:
                return Outcome(False, f"{store_name} showed the password field again after the password went in.", pause=True)
            if user and user.get("empty"):
                await cdp.type_into("user", email)
            if state.get("remember"):
                await cdp.click_tagged("remember")
            await cdp.type_into("password", password)
            await cdp.submit(state)
            typed_user = typed_password = True
        elif user and user.get("empty"):
            if typed_user:
                return Outcome(False, f"{store_name} showed the email field again after the email went in.")
            await cdp.type_into("user", email)
            if state.get("remember"):
                await cdp.click_tagged("remember")
            await cdp.submit(state)
            typed_user = True
        elif user and not pw and not typed_user:
            # The store remembered the address and wants a Continue.
            await cdp.evaluate("document.querySelector('[data-trove-auto=user]')?.focus()")
            await cdp.submit(state)
            typed_user = True
        elif state.get("authApp") and totp is not None and not chose_app:
            # The store asks which second step; the authenticator is the one
            # Trove can answer.
            await cdp.click_tagged("authapp")
            chose_app = True
            gone_reads = 0
        elif state.get("authApp") or state.get("sendCode"):
            return Outcome(
                False,
                f"{store_name} asked for a second sign-in step Trove cannot answer"
                + (" (the authenticator choice did not lead to a code field)." if chose_app
                   else ": a code sent by email. Turning on an authenticator app there, and storing its secret in Trove, would let it finish."),
                pause=True,
            )
        elif (typed_password or typed_code) and not pw and not user and not code:
            # The form is gone and nothing objected - unless the next step is
            # still loading. Humble's password step hands over to a new page
            # for the authenticator code, read empty once before its field
            # rendered (measured, 23 Nov 2026). So it has to stay gone.
            gone_reads += 1
            if gone_reads >= 2:
                return Outcome(True, "The sign-in details went in and the form went away.")
            await asyncio.sleep(STEP_WAIT_S * 2)
            continue
        elif state.get("loginFrame") and not followed_frame:
            # GOG puts its form in an iframe from login.gog.com, which a page
            # script cannot reach into; the same page opened on its own can be.
            await cdp.navigate(state["loginFrame"])
            await _wait_for_page(port)
            followed_frame = True
            continue
        else:
            empty_reads += 1
            if empty_reads >= EMPTY_READS_BEFORE_GIVING_UP:
                if typed_password or typed_code:
                    return Outcome(True, "The sign-in details went in.")
                return Outcome(False, f"Trove could not find a sign-in form on {store_name}'s page.")
        await asyncio.sleep(STEP_WAIT_S + random.uniform(0, 1.5))
    if typed_password or typed_code:
        return Outcome(True, "The sign-in details went in.")
    return Outcome(False, f"The sign-in on {store_name} did not finish within {ATTEMPT_S:.0f} seconds.")
