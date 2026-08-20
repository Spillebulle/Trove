# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Status: v0.1.0. The loop is built and the interface is complete.** Epic
discovery, the per-account scheduler, the ledger, notifications and the live
browser view all work and have been driven against the real store. The one part
that is written but unproven is Epic's *checkout*, because proving it needs a
signed-in Epic account. See "What is verified and what is not" below, and keep
that section honest: it is the difference between this file and a wish list.

Keep this file current. When you learn a store's quirk, a Playwright trap, or a
scheduling rule that is not obvious from the code, write it down here rather
than in a commit message.

## What this is

A self-hosted service that periodically signs in to the game stores the user
already has accounts with and claims the games that are temporarily free (the
weekly Epic giveaway, Prime Gaming's drops, GOG giveaways, occasional Steam
"free to keep" promotions, EA and Ubisoft freebies). It runs unattended on a
schedule, keeps a ledger of what it claimed, and has a web UI for adding
accounts, watching runs and handling the cases it could not finish alone.

Name: **Trove**. The folder is `Trove`, the app calls itself Trove, and the
mark is three stacked coins.

## The constraint the whole design turns on

**Store logins are hostile to automation, and the app must not fight that.**
Epic, Amazon and EA all use bot detection (hCaptcha / Arkose / device
fingerprinting) and will challenge a fresh login, a new IP, or a headless
browser. The app's answer is *not* to solve challenges. It is:

1. **Sessions, not logins.** Each account gets its own persistent browser
   profile (cookies, local storage, device fingerprint) and reuses it forever.
   A healthy account signs in once, by hand, and then never logs in again.
2. **A manual-attention queue.** When a run hits a captcha, a 2FA prompt, a
   "verify it's you" email or a changed login flow, it stops, screenshots the
   page, marks the account `needs attention` with the reason, and notifies. It
   never guesses, never retries in a loop, and never asks the user for a captcha
   solving service key.
3. **Human cadence.** Polite intervals (hours, not minutes), jitter, one store
   at a time per account. A claimer that hammers a store gets the user's account
   flagged, which is a far worse outcome than a missed free game.

Corollaries that must survive refactors: no captcha-solving integrations, no
credential sharing between accounts, no "solve 2FA for me" flows beyond an
optional user-supplied TOTP secret, and TOTP secrets are treated as secrets at
rest like everything else. Automating a store login can breach that store's
terms of service; the app is for the user's own accounts on their own machine,
and the README should say so plainly rather than pretending otherwise.

**The sign-in loop, and the cookie store (0.1.8).** A person signed in on the
container's screen, closed the window, and the session check reported them
signed out - and reopening the window showed them signed out too, forever.
The cause was the cookie store. Chrome on Linux encrypts the cookie database
with a key from the system keyring; a container has no keyring, so the key is
unstable between launches, and cookies written under one key are undecryptable
under the next. Playwright's *driven* browser already sidesteps this with
`--password-store=basic --use-mock-keychain` (deterministic, no keyring) - but
the *un-driven* sign-in window did not, so the session a person created by hand
was written with a keyring key the driven session-check (mock keychain) could
not read. Both browsers now carry those two flags via `browser.CONTAINER_ARGS`,
so the hand-made session survives the handoff to the run and survives a
restart. The smoke workflow proves a cookie persists across close-and-reopen
(`tools/cookie_persist.py`) and that the flags are on the un-driven window's
command line. Keep "Remember me" checked at sign-in too: without it Epic's auth
is a session cookie that no store can persist across a browser restart.

**On stored store credentials (0.1.7).** An account *may* hold an encrypted
sign-in email, password and TOTP secret, and the rule about them is narrow
and must stay narrow: they are typed into the un-driven sign-in window on the
container's screen **when the person presses a button** (`POST
/api/accounts/{id}/type`, which refuses unless that account's own sign-in
window is open), the way a password manager types into a form. **No run, no
scheduler, no adapter may ever read them.** They exist so that, signing in
through a remote picture, the captcha is the only thing that needs a person
and a forty-character generated password is not typed key by key. A stored
password used to log in unattended would be the thing bot detection is
looking for, and it would undo the design; this is not that.

## Design

UI follows `../Design-Principles/STYLE-GUIDE.md` and uses its `tokens.css`;
**accent hue is 310** (orchid: `#AA85C5` dark, `#83609D` light). Hosted web app,
so `<html class="web">` for the one step up in size (§6.5). Never a raw hex in a
component.

310 was picked because it collides with no sibling. The hues actually in use,
read from the files rather than from the guide: Umber 68 (ochre), Tally 255
(`frontend/src/theme-tally.css`; its copied `tokens.css` still carries the
default 68, which is overridden and not what ships), HomeLab Manager **349.1**
(`frontend/static/tokens.css`, a crimson at chroma 0.221 — note the style
guide's §2.3 still *suggests* 160 sage-teal for it, which is stale), plus
Umber's shipped user accents Sage 124, Steel 258, Clay 20. 310 is a violet, far
enough from HomeLab's pink-red to read as a different app at a glance and 55°
off Tally's blue. It also stays clear of the semantic colours, which
matters here more than in the other apps: this app's screens are mostly *state*,
and `good` (sage) / `caution` (clay) / `critical` (muted red) are already spoken
for by claimed / needs attention / failed. The accent must never be used to mean
a status; it means selected, in hand, primary (§2.4).

Read the checklist in §16 of the style guide before the first screen ships, and
§17 before the README.

## The stack

Follow Tally's shape, which is the house pattern for a hosted app in this
family: FastAPI + SQLAlchemy + SQLite on the backend, React + TypeScript +
Vite + Tailwind on the frontend, built into one Docker image where the API also
serves the built SPA. HomeLab Manager is the reference for the pieces Tally does
not have: the adapter registry, the background poller, Fernet-encrypted
credentials, single-user cookie auth with a bcrypt password.

Two additions neither sibling has, both now built:

- **Playwright (Python)**, one persistent context per account, profiles under
  `<DATA_DIR>/profiles/<id>-<slug>`. Headed by default; the container puts up an
  Xvfb display for it. **Driving real Google Chrome, not the bundled
  Chromium** - see "The browser has to be real Chrome" below, which is the most
  important paragraph in this file. `backend/app/browser.py` owns this, including the
  `asyncio.Lock` per account that stops a scheduled run and the live view
  opening the same profile at once. **Chromium corrupts a user-data directory
  two processes have open**, so every path to a profile goes through
  `manager.session()` and nothing else may launch a browser.
- **The interactive login path is a CDP screencast, not noVNC.**
  `backend/app/live.py` attaches a CDP session to the account's page,
  `Page.startScreencast` streams JPEG frames over a WebSocket, and
  `Input.dispatch*` sends clicks and keys back. The frontend half is
  `components/LiveBrowser.tsx`.

  It was chosen over noVNC because Playwright is already speaking CDP: no second
  process, no second port, no VNC password to get wrong, and the whole feature
  is two files. The costs are real and worth knowing: it is Chromium-only (the
  app already was), and it renders one tab rather than a desktop, so a store
  that opens a popup window would not be visible. Epic does not.

  Three traps, all commented where they happen:

  1. **Frames must be acknowledged.** Chromium sends exactly one more frame per
     `Page.screencastFrameAck`. Miss it and the live view shows a single still
     image forever. The ack happens *after* the frame is sent down the socket,
     which is also what makes the stream self-pacing.
  2. **Text goes through `Input.insertText`, never synthesised key events.**
     Reproducing a keydown faithfully means reproducing dead keys, AltGr, phone
     keyboards and paste. Only the keys with no text (Enter, Tab, Backspace,
     the arrows) are dispatched as keys. Verified with an umlaut and a tick.
  3. **The canvas needs somewhere to put the keyboard.** An off-screen textarea
     takes focus on click; `display: none` cannot hold focus and receives no
     keys.
  4. **The canvas element's box must equal the drawn bitmap.** A canvas is a
     replaced element, so `object-fit: contain` letterboxes the 1280x800 frame
     *inside* the element rather than resizing the element. The click mapping
     goes through `getBoundingClientRect()`, which is the element - bars
     included - so coordinates were offset by the bar and scaled by the wrong
     ratio.

     Measured on a 964x503 element drawing an 805x503 image: **0 px error at
     the horizontal centre, +/-84 px at the edges.** That is why it presented
     as "clicking works sometimes" rather than as an obvious fault, and why a
     challenge checkbox left of centre was unhittable while the middle of the
     page was fine. Use `max-h-full max-w-full` and let the intrinsic size do
     the scaling; the element box is then the drawn area and the mapping is
     exact. `scratchpad`-style measurement beats eyeballing it: the error is
     invisible until it is printed.
  5. **Acknowledging a frame immediately means rendering flat out.** Chromium
     sends one frame per `Page.screencastFrameAck`, so acking the instant a
     frame is sent asks for the next one at once, and a store page with a
     carousel renders as fast as the machine can. **Measured on the Epic store:
     113 frames a second and 7.7 MB/s, which is 62 Mbit/s.** On a LAN nobody
     notices; over the internet the frames queue in buffers and the picture
     falls further behind every second, which is what "huge delay" turns out to
     mean.

     The cap is the *acknowledgement*, not the send: hold it to
     `MIN_FRAME_INTERVAL_S` and the whole pipeline is bounded at its source,
     before a frame is ever encoded. Delaying the send instead would add
     latency to the picture on screen now, which is backwards. Re-measured
     after: 11 fps and 431 kB/s, eighteen times less traffic, and signing in
     feels the same. `live_max_fps`, `live_quality` and `live_max_width` are
     the knobs; raise them on a LAN.
  6. **Pointer moves must be coalesced.** A browser fires `pointermove` at 60
     to 120 Hz and each one became an `Input.dispatchMouseEvent` on the *same*
     CDP channel the frame acknowledgements use, so moving the mouse competed
     with the picture. Keep the newest and flush it on `requestAnimationFrame`;
     the intermediate positions describe a path nothing reads. A press or a
     release flushes the queued move first, so the button never lands at a
     stale position.
  7. **Frames go over the socket as binary.** Chromium hands them over base64,
     and forwarding that inside JSON kept the 33 % inflation plus a large
     string allocation per frame at both ends. Decode once on the server,
     `send_bytes`, and let the client use `createImageBitmap`, which decodes
     off the main thread. Control messages stay JSON and the client tells them
     apart by payload type.
  8. **Focus the keyboard sink with `preventScroll`.** Focusing an element
     scrolls it into view, and the off-screen textarea sat at `left: -9999px`,
     so a press could scroll its own container and move the canvas out from
     under the pointer between `pointerdown` and `pointerup`. It now lives at
     the container's origin at 1px with `pointer-events-none`, and is focused
     with `{ preventScroll: true }`.
  9. **A mouse move must not carry a button.** `MouseEvent.button` is `-1` on a
     move where nothing changed, and the first version looked that up in a table
     of button names with `left` as the default. Chromium derives `buttons` from
     `button` when the field is absent, so **every pointer movement arrived at
     the page as `mousemove buttons=1`** - the left button held down for the
     whole journey across the page - and the press then fired `selectstart`,
     because the renderer thought a text-selection drag was beginning. Measured
     by replaying both payload shapes against a page that logs its events.

     Send `buttons` explicitly from the DOM event, resolve a move with nothing
     held to `none`, and give a move `clickCount: 0`. A challenge that scores
     pointer behaviour is being shown a drag-then-click otherwise, which is the
     shape of a bot and not of a person.

## The browser has to be real Chrome

**Playwright's bundled Chromium cannot get past Cloudflare's interactive
challenge, and no amount of input fidelity changes that.** This cost a session
to find and is the single most important thing in this file.

The symptom: a person clicks "Verify you are human" in the live view, the
widget says "Verifying...", and ten seconds later the checkbox comes back
unticked. Forever. The click is not the problem - it plainly registers.

The cause was visible in the browser console all along:

```
Failed to parse audio contentType: audio/mp4; codecs=mp4a.40.2
Failed to parse video contentType: video/mp4; codecs=avc1.42E01E
Failed to parse video contentType: video/mp4; codecs=hev1.1.6.L93.B0
```

Turnstile probes for the proprietary codecs. Measured side by side:

| | bundled Chromium | real Chrome |
|---|---|---|
| user-agent claims | `Chrome/131.0.6778.33` | `Chrome/151.0.0.0` |
| `userAgentData.brands` | Chromium, Not_A Brand | **Google Chrome**, Chromium |
| H.264 / HEVC / AAC | **no, to all of them** | probably |

A browser that says it is Chrome and cannot play what Chrome plays is not
Chrome, and that contradiction cannot be patched over from inside the page.
So `config.browser_channel` defaults to `auto`, `browser.resolve_channel()`
tries `chrome` first and falls back to the bundle with a loud warning, and the
Dockerfile runs `playwright install chrome`. With Chrome, the same machine and
the same address that had been challenged repeatedly went straight into the
store with no interstitial at all.

**Two traps that come with it.**

1. **`--user-data-dir` must be absolute.** Real Chrome *refuses* a relative
   one: it puts up a modal saying it cannot read and write its own data
   directory and then hangs, which Playwright reports only as a launch
   timeout. `DATA_DIR` defaults to `./data` on Windows, so this bites
   immediately. `settings.profiles_path` resolves, and `session()` resolves
   again before launching.
2. **Do not chase this with stealth patches.** The fix was to stop lying about
   which browser it is, which is the same principle as everything else here:
   remove the signal, never forge one.

**And real Chrome was still not enough.** With Chrome the codec probes pass and
the store loads with no interstitial, but where Cloudflare *does* decide to run
an interactive challenge, the live view still cannot answer it: the next thing
the widget complained about was `No available adapters.` (WebGPU), and behind
that there is always another signal, because `cdpDetected` is `true` and a
screencast cannot exist without the protocol attached. That is structural. Two
sessions were spent proving it one fingerprint at a time; do not spend a third.

## Signing in without any automation at all

The live view is a **fallback**, not the main way in. It cannot be the main way
in, because streaming a page requires the DevTools protocol, a page can tell the
protocol is attached, and a challenge that has decided a browser is automated
will not accept an answer from it however honestly a person clicks.

So `POST /api/accounts/{id}/sign-in-here` opens the account's profile in an
**ordinary browser window with nothing attached to it**. `browser.launch_detached`
starts Chrome as a plain subprocess and Trove then has no connection to it at
all. Verified by reading the process's own command line back from Windows:

```
chrome.exe "--user-data-dir=<absolute profile>" --no-first-run --no-default-browser-check <url>
```

No `--enable-automation`, no `--remote-debugging-port` or `-pipe`, no
`--headless`, and no `--disable-blink-features` because there is nothing to
hide. It is a person using Chrome on their own computer, which is exactly what
it is. When they close the window the process exits, the per-account lock is
released, and the profile holds the session every later run reuses.

Rules that keep it honest:

- It takes **the same lock** every other path takes (`manager.open_local`), so a
  scheduled run cannot open the profile while somebody is signing in to it. Two
  Chromiums sharing a user-data directory corrupt it.
- The window is **detached**, so restarting Trove does not kill a half-finished
  sign-in.
- `GET /api/accounts/{id}/can-sign-in-here` answers whether this is possible at
  all - headless mode, no Chrome, or no `DISPLAY` all mean no - so the interface
  can offer the live view instead rather than a button that cannot work.
- In a container there is no screen, so the live view really is the only option
  there, and it is worth saying plainly that a captcha may be unanswerable in
  that deployment. The honest workaround is to sign in on a desktop and copy the
  profile directory across.

## What Docker can and cannot do

Trove runs in a container, and since the screen view landed **the first
sign-in can happen there too** - on the container's own display, through a
browser with nothing attached to it. Whether the store then lets that browser
in depends on things Trove does not control, chiefly the address, so the
profile-copy path below is still the fallback.

Works in the container: discovery, the scheduler, the ledger, notifications,
the whole interface, and claim runs on an account whose session is already
good. That is the unattended half, which is most of the app's life.

**"Sign in here" in a container goes through the screen view.** The entrypoint
puts `x11vnc` on the Xvfb display (`VNC_ADDRESS=127.0.0.1:5900`, localhost,
no password), `routers/screen.py` bridges Trove's own authenticated WebSocket
`/api/screen` to that port byte-for-byte, and `components/ScreenView.tsx` is
noVNC's RFB client drawing it. `sign-in-here` then does exactly what it does
on a desktop - `launch_detached`, a plain Chrome subprocess with no CDP and no
automation flags - on `DISPLAY=:99`, and the person works it through the
screen. `can-sign-in-here` answers `via: "desktop" | "screen"` so the
interface knows whether to expect a window or to open the screen dialog.
`close-sign-in` terminates that Chrome politely, because the screen has no
window manager and there may be nothing to click. The smoke workflow opens
and closes one and reads its command line back: no `--remote-debugging`, no
`--enable-automation`, no `--headless`. The window opens `--start-fullscreen`
(F11, not kiosk) so the small picture is all page and the address bar and
the `--no-sandbox` infobar are out of the way.

**Watching a run** (0.1.9). A claim run is headed on the same Xvfb display the
screen view streams, so "Run and watch" (`POST /run?watch=true`) opens the
screen view and the person sees the run happen: the store loads, the session
is checked, the checkout is tried. A watched run **holds the browser open**
when it finishes or fails - `runner._hold_open_for_watch`, inside the
`manager.session` block so the window does not close - until Done
(`/stop-watching`, `runner.release_watch`) or a 300 s cap. That is what makes
the checkout fixable: it is the one part no test can reach, and now a person
can read the exact page Epic showed rather than a screenshot after the fact.
The stop is sticky (`_watch_stop`) so a Done pressed before the hold starts is
not lost. Outside watch mode none of this runs.

**Interactive captchas at checkout (hCaptcha / Talon).** Epic can raise an
image hCaptcha the instant "Add to library" is pressed, and its iframe covers
the button - which made the naive click loop for 45 s and die with a raw
`TimeoutError`. The rule is unchanged and absolute: **Trove never solves a
captcha.** What it does now: the checkout click is captcha-aware (`epic._click`), so a
blocked click is recognised as a challenge rather than hammered. **Every run -
watched or scheduled - gets a `runner._CaptchaWaiter`** (handed to
`adapter.claim` as the `ChallengeWaiter`), so a captcha pauses the run for up to
`CAPTCHA_WAIT_MAX_S` (5 min) with the browser held open on the container's
screen rather than failing outright, and the person solves the hCaptcha there;
the moment it clears the claim resumes on its own. They reach it by **jumping
into the run in progress** - the account page's "Watch" / "Solve the captcha"
button opens the screen view on the live run without starting another (observe
mode: closing it does not stop the run). `waiting_for_captcha` on the account
read drives that button and the banner, and a notification goes out when the
pause begins - **with a screenshot of the captcha attached** (`Notification.
image_path`, uploaded to Discord as a multipart file because Discord fetches
URLs from its own side and cannot see a local one; context, not a puzzle to
solve). If nobody comes, the wait times out to `NeedsAttention` as before.

**Superseded for Epic (0.1.19):** the paragraph above describes the general
captcha-pause the `_CaptchaWaiter` provides, and it is how the app *tried* to
answer Epic's checkout captcha - a solve on the driven screen. That solve is now
proven not to work (`confirm-order` returns `epic.error.captcha.challenge.failed`;
see below), so `epic._handle_challenge` no longer waits on the waiter: it raises
`CheckoutBlocked` and the person finishes in the un-driven window instead. The
waiter machinery stays in `runner.py` for any future store where a driven solve
might work, but Epic does not use it, so `waiting_for_captcha` and the "Solve
the captcha" jump-in button do not light up for Epic.

**Talon does NOT accept a human solve in the driven browser. Proven, and it
settles the open question (0.1.19).** A person solved Epic's checkout captcha in
a watched run - shown over **VNC (pixels off the X server), not a screencast**,
so during the solve Playwright is doing nothing over CDP and the page's
automation tells (`navigator.webdriver` false, no `Runtime.enable` console leak
on Chrome 151) are quiet. It made no difference. The order request that follows
the solve returns, measured from a real account:

```
POST https://payment-website-pci.ol.epicgames.com/v2/purchase/confirm-order
  -> HTTP 400   captcha-token-in-request=True
  {"errorCode":"epic.error.captcha.challenge.failed", ...}
```

The token **was** attached (the solve produced one); Epic rejected it. `400`,
not `403` - the browser is not blocked outright, its *captcha solve* is refused,
because Talon scored the environment as automated. `cdpDetected` is true and a
run cannot exist without CDP attached, so this is structural, exactly as the
live view's version of it was. A human solve in the driven browser is a dead
end for checkout; do not try to make it pass, and do not confuse the `400`
(solve refused) with a `403` (would be a hard block).

**So the fallback CLAUDE.md reserved is now built: finish the checkout in the
un-driven window.** When Epic raises a captcha at checkout, `epic._handle_challenge`
no longer waits for a driven solve (`_CaptchaWaiter`, now unused by Epic) - it
raises `browser.CheckoutBlocked`, a `NeedsAttention` subclass carrying the
offer's id. The runner records `account.checkout_offer` and files the account
with the remedy, and the account page shows a primary **"Finish the claim
here"** button. That button (`POST /api/accounts/{id}/finish-claim`) opens the
account's profile in the **same un-driven window as sign-in** (`manager.open_local`,
holder "a sign-in window", no CDP), only pointed at the offer's
`/purchase?offers=…` page via `adapter.checkout_url`. The person presses "Add
to library", answers the captcha and accepts *in the browser Epic's captcha
already trusts*, and closes the window. `runner.verify_checkout` then asks the
store whether it worked (one `is_owned` load, no assuming), writes a `claimed`
ledger row and clears `checkout_offer` if so, or leaves the button in place to
retry if not. On a desktop the window is in front of the person; in a container
it is on the Xvfb and they work it through the screen view - the exact machinery
sign-in already uses, copy aside. `checkout_pending` on the account read drives
the button. **Built, and the claim-sim covers both branches (no-captcha ->
claimed, captcha -> CheckoutBlocked); the end-to-end un-driven claim is not yet
reported from a real account.**

**The checkout is a phase-aware loop (`epic._drive_checkout`).** Epic
interleaves the steps differently per title and a captcha can land between any
two, and a real trap surfaced against a live account: **the add-to-library
button lingers on the page behind the "Right of Withdrawal" dialog while the
order is processing.** A loop that clicks "whatever is next" clicked it a
*second* time, racing the first order, and Epic answered with "An error occurred
while trying to process your request." So the loop now has phases: in `start`
it clicks the add-to-library button *once* (`PLACE_ORDER`); a consent dialog
(`ACCEPT` - "I accept", dialog-scoped so it never hits the "Cancel" beside it)
is answered whenever it appears; and once the order is placed it **never touches
the add-to-library button again** - it only answers a dialog, surfaces an
`ERROR` toast as `NeedsAttention` (with a screenshot), or waits for
`CONFIRMED`/`OWNED`. `tools/checkout_sim.py` proves both branches: a no-captcha
checkout clicks the order button exactly once even while it lingers in a
"processing" state, and a checkout captcha raises `CheckoutBlocked` (naming the
offer) rather than hammering the button or reporting a false claim. The captcha
and the "I accept" step are seen against a real account; a confirmed end-to-end
claim is still to be reported - now via the un-driven window (above), since the
driven checkout cannot pass Epic's captcha.

**The checkout is the live-fix surface.** `adapters/epic.PLACE_ORDER` (and
AGREEMENT, CONFIRMED, OWNED) are a deliberately long list of selectors because
Epic renames the order button constantly; when a claim stops with "could not
find the button that places the order", watch a run, read the real label off
the screen, and add it there. The claim now logs each checkout step, so the
container log narrates it too.

**Typing into that window** is `keyboard.py`: `xdotool type --file -` through
the X server (XTEST) into the focused window, which on that screen is the
un-driven Chrome; the text goes on stdin, never argv. `POST
/api/accounts/{id}/type` with `email | password | code | enter | tab` types
the stored detail (the TOTP code is computed in `crypto.totp_code`, RFC 6238,
checked against the RFC's vectors) and the screen view has a button per
item. It works only while that account's sign-in window holds the profile and
only where there is a DISPLAY and xdotool, which is the container.

Why VNC here when the live view was deliberately *not* VNC: the live view's
objection to noVNC was a second process for a picture CDP already provided.
The screen view exists for the opposite reason - to show a browser that has
**no** CDP on it, which a screencast by definition cannot. They are not two
ways of doing one thing; the live view is a tab over the protocol, the screen
is pixels off the X server. `settings.has_visible_desktop` still answers "is
there a screen in front of the person" and is still false in a container;
`settings.has_screen_view` is the new, separate question.

**The browser in the container has a GPU now, a software one.** Under Xvfb
with no GPU, Chrome turns WebGL off and reports no WebGPU adapter, and `No
available adapters.` is the exact line a Cloudflare challenge logged in the
live view before refusing every answer. A browser whose user-agent says desktop
Chrome and whose WebGL does not exist is the same kind of contradiction as the
codec one. The image installs Mesa (`libgl1-mesa-dri` for llvmpipe,
`mesa-vulkan-drivers` for lavapipe), Xvfb runs with `+extension GLX`, and
`browser.CONTAINER_ARGS` adds `--ignore-gpu-blocklist --enable-unsafe-webgpu`
to both the driven browser and the un-driven window. This is restoring a
capability the browser is supposed to have, not forging a signal - the same
principle as driving real Chrome. Measured in the container by the smoke
workflow: WebGL renderer `ANGLE (Mesa, llvmpipe (LLVM 20.1.2 256 bits),
OpenGL 4.5)`, WebGPU adapter `google / swiftshader`, H.264 and AAC `probably`,
HEVC empty (Linux Chrome has no HEVC; that is normal and not a tell).

**`python -m app.diagnose` is how any of this gets checked.** It launches a
throwaway profile exactly the way a run does and prints what a page sees:
brands, codecs, WebGL, WebGPU, focus, screen, and whether the Runtime-domain
console leak fires. `GET /api/diagnostics/browser` is the same thing behind
the "Check the browser" button in Settings. Run it before trusting any claim
about the browser, and put its output in this file rather than an impression.
One measured fact from it already: **the classic CDP tell - an Error `stack`
getter firing on `console.debug` while Runtime is enabled - does not fire on
Chrome 151**, with `Runtime.enable` sent explicitly, for any console method.
That specific leak is gone from current Chrome; it does not follow that CDP
is undetectable, and the un-driven window stays the way in.

- **The live view works but may not be enough.** It can render a captcha and
  take clicks, and for a store that only wants a password that is fine. For an
  interactive Cloudflare challenge it is not, for the reasons in the two
  sections above.
- **The address matters more than anything Trove does.** A VPS or cloud
  address is challenged far more readily than a home one, and no browser
  change fixes that. If the container lives on a datacenter address, route its
  egress through a residential connection (a Tailscale exit node at home, a
  WireGuard tunnel) or run it at home. `BROWSER_PROXY` (`socks5://host:port`)
  is the in-app version of that: it is applied to the driven browser *and* to
  the un-driven sign-in window, so the session is made and reused from one
  address - a session created at home and replayed from a datacenter is
  itself a signal. An SSH `-D` tunnel back to a home machine is the cheapest
  way to get one.

**The fallback is still to sign in on a desktop and carry the profile over.**
A profile is an ordinary directory:

```
<DATA_DIR>/profiles/<id>-<slug>/
```

Copy that directory into the container's volume at the same path, then press
"Check again" on the account. `runner.check_session` asks the store rather than
assuming, so a profile that did not survive the trip says so instead of being
trusted. The id in the directory name must match the account's, which is why
the name is never re-derived from a renamed label.

Three container-specific things that are easy to get wrong, all now handled:

1. **`--no-sandbox` is added only in a container, and only if needed.** It
   used to be unconditional there, on the reasoning that Chrome's sandbox needs
   user namespaces the default seccomp profile withholds, so without the flag
   Chrome does not start - and `resolve_channel` reads that as "Chrome is not
   usable" and silently falls back to the bundled Chromium, exactly the browser
   that cannot pass a captcha. `CONTAINER_SANDBOX` defaults to `auto`:
   `resolve_channel` probes each channel with the sandbox first and
   `--no-sandbox` second, commits the flag to `LAUNCH_ARGS` and
   `CONTAINER_ARGS` only if the first would not start, and logs which.
   `open_local` resolves before opening the un-driven window so the first
   sign-in of a fresh install does not open a Chrome that dies on the sandbox.

   **A trap found on the way: Playwright adds `--no-sandbox` itself** unless
   `chromium_sandbox=True` is passed (its default is false). The first version
   of the probe "launched fine with the sandbox" every time because it never
   had one, the smoke run reported `sandbox: on`, and the un-driven window -
   which gets only the flags it is given - then died silently on the real
   sandbox. Every Playwright launch now passes `chromium_sandbox` explicitly
   to match what was decided, so the probe measures Chrome and not Playwright.
   The `--no-sandbox` infobar across the top of the screen view is the
   visible difference; the page cannot see the flag either way.
2. **The entrypoint must not need a package the image lacks.** It waited on
   `xdpyinfo`, which lives in `x11-utils` and is *not* installed by `xvfb`; a
   missing command returns non-zero forever, so the readiness loop timed out and
   the entrypoint exited 1. **The container never started.** It now waits on the
   X socket at `/tmp/.X11-unix/X<n>`, which needs nothing.
3. **The image installs real Chrome** (`playwright install --with-deps chrome`)
   for the codec reason above. Without it the container has only the bundle.

**The container has now been started, in CI.** There is still no Docker on the
development machine, so `.github/workflows/container-smoke.yml` is how the
image gets run: it builds it, starts it, checks Xvfb and x11vnc came up,
runs `python -m app.diagnose` inside it as `pwuser`, and opens and closes a
sign-in window through the API. It runs on every push to `main` and to
`container/**` branches, and its log is the measurement; read it before
changing anything in the Dockerfile or the entrypoint. What it cannot do is
sign in to a store, so a claim run in the container is still unproven.

## Architecture

Four concerns, kept thin, mirroring HomeLab's split. Where each one lives:

| Concern | Files |
|---|---|
| HTTP/CRUD | `routers/accounts.py`, `routers/ledger.py`, `routers/settings.py`, `routers/live.py`, `main.py` |
| Scheduler | `scheduler.py`, and `runner.py` for what one run does |
| Store adapters | `adapters/base.py` (the contract), `adapters/epic.py`, `adapters/__init__.py` (`ADAPTER_MAP`) |
| Ledger | `models.py`, and the pages that are views of it |
| The browser | `browser.py` (profiles and the lock), `live.py` (the screencast) |

- **HTTP/CRUD** — accounts, claims, runs, settings, and the SPA.
- **Scheduler** — a per-account loop, not a global tick: each account has its own
  interval and its own last-run time, and one slow store must not delay another
  account. HomeLab's `poller.py` is the model, including the graceful-shutdown
  handling of in-flight child tasks. `next_run_at` is **persisted**, not
  computed from `last_run_at` plus an interval, so a restart cannot re-roll the
  jitter and bunch every account onto the same minute.
- **Store adapters** (`adapters/`) — one per store behind a small contract, with a
  single `ADAPTER_MAP` registration point: `list_free_offers()`, `health(page)`,
  `is_owned(page, offer)`, `claim(page, offer)`. Every adapter declares what it
  needs the way HomeLab's adapters declare service requirements, and
  `/api/accounts/stores` reads that declaration so the add-account page explains
  itself with no second list to keep in step.

  Two rules for an adapter: it may not import the database, the scheduler or
  FastAPI, and `is_owned` returns **`None` when it could not tell**, which is a
  different answer from `False`. Claiming something already owned is harmless;
  recording "not owned" when the check failed teaches the ledger a lie.
- **Ledger** — every attempt is a row: account, store, offer, outcome
  (`claimed` / `already_owned` / `not_eligible` / `needs_attention` / `failed`),
  timestamp, and the screenshot path when it stopped. The UI is a view of this
  table; the app never claims a game it cannot show a row for. `Claim.title` is
  **copied** off the offer rather than joined, so the ledger keeps reading
  properly after an old offer row is pruned.

The ledger is also the memory that stops a double claim: `runner._already_claimed`
skips any offer this account already has a `claimed` or `already_owned` row for,
so a manual run, a scheduled run and a restart cannot combine to claim one thing
three times.

Discovery and claiming are separate steps, and this is the boundary to defend.
`runner.discover()` asks the adapter what is free, which for Epic is one request
to a public endpoint: no browser, no session, no account. A run that finds
nothing to claim ends there having cost one HTTP request. `POST
/api/offers/refresh` is the same path, which is why the button in the interface
is safe to press and says so.

The public feed (`discovery.feed_enabled`) is a setting that exists and is off.
GamerPower is the obvious candidate and **its terms have not been checked**. If
it is added: a broken feed must degrade to "check the stores on schedule", not
to a stopped app.

### Per-store notes

- **Epic Games Store** — built. Discovery is the public `freeGamesPromotions`
  endpoint and is verified; claiming drives the browser to
  `store.epicgames.com/purchase?offers=1-<namespace>-<offerId>` rather than
  clicking "Get" on the product page, because the product page renders the same
  checkout in a cross-origin iframe and driving that breaks whenever Epic
  changes the container.

  Three things learned by running it. The `-ipv4` promotions host is deliberate:
  the plain hostname resolves to an IPv6 address a default Docker network cannot
  reach, which presents as a hang rather than an error. **Epic serves
  Cloudflare's interstitial readily**, so the first thing a new account often
  meets is a challenge rather than a login page; that is the live view's job.

  And **Cloudflare's verdict follows the profile, not just the address.** A
  fresh profile from this machine was waved straight through to the store with
  no interaction at all, while an older profile on the same machine and the same
  address kept being challenged. A profile that has been refused once tends to
  keep being refused, and answering the challenge again does not clear it -
  which is what `POST /api/accounts/{id}/reset-profile` exists for. Reach for
  that before reaching for a stealth plugin.
- **Prime Gaming** (what the user called Twitch Prime; Twitch Prime was renamed
  years ago) — needs an active Amazon Prime subscription. Many of its offers are
  *keys for other stores* (GOG, Epic, Legacy Games) rather than a library add, so
  the ledger has to be able to record "claimed a key" and show the key to the
  user. Amazon's login is the most aggressive of the set.
- **GOG** — occasional giveaways, usually a simple claim on the promo page.
- **Steam** — has no weekly giveaway; what exists is "free to keep" promotions
  and free-to-play adds, claimed from the store page. Do not build Steam as if
  it were Epic. Steam Guard makes an unattended login unrealistic, which is
  another reason sessions matter more than credentials.
- **EA (EA app / Origin)** and **Ubisoft Connect** — infrequent giveaways; worth
  an adapter only after the first two work end to end.

One store, one account, one full loop is done. **A second adapter should still
wait** until Epic's checkout has claimed something real, because the checkout is
the only part of the contract no adapter has exercised yet, and a second store
written against an unproven shape is a second store to rewrite.

## Commands

Verified by running them. The user develops on Windows 11 with PowerShell, so
the local-dev commands are PowerShell.

```powershell
# --- First time --------------------------------------------------------------
python -m venv backend/.venv
backend/.venv/Scripts/pip install -r backend/requirements.txt
backend/.venv/Scripts/python -m playwright install chromium
cd frontend; npm install; cd ..

# --- Local dev, two terminals ------------------------------------------------
# API on 8080, auto-reloading. DATA_DIR keeps the database, the profiles and the
# screenshots inside the repo rather than at the drive root, which is where a
# bare "/data" lands on Windows.
$env:DATA_DIR="./data"; backend/.venv/Scripts/python -m uvicorn app.main:app --app-dir backend --reload --port 8080

# Vite on 5173, proxying /api (and the live-view WebSocket) to 8080.
cd frontend; npm run dev

# --- The production layout, without Docker -----------------------------------
# The API serves the built SPA out of backend/app/static, so a build is a copy.
cd frontend; npm run build; cd ..
Remove-Item -Recurse -Force backend/app/static -ErrorAction SilentlyContinue
Copy-Item -Recurse frontend/dist backend/app/static

# --- Docker ------------------------------------------------------------------
# The published image, amd64 only. `:main` is the tip of the branch and is what
# to use while the first Linux deployment is still being debugged.
docker compose pull; docker compose up -d
docker compose logs -f trove

# Building it locally instead (put `build: .` back in docker-compose.yml):
docker compose up -d --build
```

**Run uvicorn from the repository root**, not from `backend/`. `.env` and
`.env.local` are resolved relative to the working directory, so starting it a
directory down silently ignores them and you get the "changeme" password and no
CORS origin.

`npm run lint` is `tsc --noEmit`. There is no test suite yet; when there is one,
put how to run a single test here.

## What is verified and what is not

The honest ledger for this file. Keep it accurate; it is what stops the next
session trusting something nobody has run.

**Verified against the live service or the running app:**

- Epic's `freeGamesPromotions` endpoint, its shape, and the two-condition
  filter. Both conditions are needed and the reason is in `adapters/epic.py`:
  a currently-running promotion can still list a full price, and a zero
  discount price can belong to *next* week's giveaway.
- Slugs. Of eleven listed promotions, seven had a null `productSlug` and every
  one of them had a `catalogNs` mapping, so `_product_url` tries three fields.
- The live view: frames arrive, a click reaches the remote page, and Epic's
  Cloudflare "verify you are human" step renders and can be answered.
- `Input.insertText`, control-key dispatch and screencast acknowledgement,
  checked directly against a page with a real field.
- A full run: discovery, browser open, health check, `NeedsAttention` raised on
  Epic's challenge, run marked `attention`, account marked with a reason and a
  screenshot, no retry. This is the design working, not failing.
- Notifications: both payload shapes, the 404 / unreachable / no-URL branches,
  and that a saved webhook is never returned to the browser. The Discord side
  is a branded rich embed - the webhook wears Trove's avatar and name
  (`username`/`avatar_url`), a colour per severity, an author row with the mark,
  and for a claim the game as the linked title with its poster as the embed
  image and the store and account in the footer. The avatar and author/footer
  icons are `docs/brand/avatar.png` fetched over a raw GitHub URL (Discord
  fetches images from its own side, so a local path cannot work). Payload
  validated and a rendered mock reviewed; not yet sent to a live webhook.
- Both themes, and the drawer below 1024px.
- The mouse bridge, by replaying both payload shapes against a page that logs
  its own events: moves are hovers, the press produces one trusted click, and no
  drag or `selectstart` is started.
- That a *fresh* profile is let through Epic's interstitial where an older one
  on the same machine is not.
- **That Epic refuses a checkout-captcha solve done in the driven browser**, by
  the `confirm-order` response captured from a real account: `HTTP 400
  epic.error.captcha.challenge.failed`, with the token present. This is what
  the whole un-driven "Finish the claim here" path exists to answer, and it is
  the first hard measurement that the driven browser cannot complete a checkout
  that raises a captcha - a person clicking honestly on the screen does not
  change it.
- **That the bundled Chromium is the reason a captcha could not be answered**,
  by reading the codec support of both browsers directly, and that with real
  Chrome the store loads with no interstitial at all. The console lines that
  gave it away came from the user, not from a test.
- That real Chrome refuses a relative `--user-data-dir`, from the dialog it
  puts up before Playwright times out.
- That the un-driven sign-in window carries **no** automation flags, by reading
  its command line back out of `Win32_Process` after launching it, and that it
  writes a usable profile.
- **The whole sign-in loop, on a real Epic account.** A person signed in through
  the un-driven window; Playwright-driven Chrome then opened the same profile
  and `adapter.health` reported the account signed in, so the run scheduled
  itself. That is the design's central claim - sessions, not logins - working
  end to end for the first time.
- That Epic's redirects make `page.goto` raise `net::ERR_ABORTED` on a page that
  in fact loaded. `adapters/epic._goto` tolerates it; without that, a healthy
  signed-in account read as a 500.
- **The container starts and the browser in it is whole**, by the smoke
  workflow: the entrypoint brings up Xvfb and x11vnc, the API answers, real
  Chrome 151 launches headed under Xvfb as `pwuser` with `--no-sandbox`,
  WebGL reports Mesa llvmpipe, a WebGPU adapter exists, H.264 and AAC play,
  `navigator.webdriver` is false, the brand list says Google Chrome, and an
  un-driven sign-in window opens on the container's screen with no automation
  flags on its command line and closes again through the API.
- The screen bridge, against a fake VNC server: the greeting comes through,
  bytes go both ways, and an unauthenticated socket is refused before any
  connection to the VNC port is made.
- **What the store shows the container's un-driven window, from a datacenter
  address.** The smoke workflow captures the container's display over its VNC
  port after "sign in here" (`docs/images/08-container-screen.png`, taken on
  a GitHub Actions runner): real Chrome on the Xvfb, the Epic store front page
  fully rendered, a "Sign in" button, and **no Cloudflare interstitial at
  all** - from an address that is about as datacenter as an address gets. One
  sample, not a promise; but it is the first time this app has had a picture
  of a store letting the container in rather than an argument about whether
  it would. The only blemish is Chrome's own "--no-sandbox" infobar across the
  top, which the page cannot see and a person can close - and which goes away
  wherever the sandbox probe finds Chrome can keep its sandbox. (On the
  runner it cannot; see the trap under "three container-specific things".)

  **And the driven browser too.** When that window closed, Trove opened the
  same profile with Playwright - CDP attached, the exact launch a scheduled
  run uses - to ask Epic whether it was signed in. The screenshot that check
  left behind (`0001-epic-…-signin-check.png` in the smoke artifact) is the
  Epic store front page, fully rendered, "Sign in" button and all: **no
  interstitial for the driven browser either**, from the same runner address.
  The check then correctly reported "signed out" and filed the account for
  attention, which is the design working. So the measured state of the
  container is: real Chrome, software GPU, both the un-driven window and the
  driven run reach the store from a datacenter address without a challenge.
  Whether that holds at the *login* and the *checkout* is what a signed-in
  account will tell; those pages are where Epic challenges hardest.
- **A cookie survives close-and-reopen with the run's launch args**
  (`tools/cookie_persist.py` in the container), and the un-driven window
  carries `--password-store=basic --use-mock-keychain` on its command line.
  This is the sign-in-loop fix; the workflow asserts both.
- **The sign-in window opens on Epic's real login page, full screen.** The
  capture shows `www.epicgames.com/id/login`, email-first (one field and a
  Continue button; the password field appears only after Continue), the email
  field autofocused, no address bar and no infobar. So the assisted sequence -
  email, Enter/Continue, wait, password, Enter - matches Epic's actual flow,
  and a blind five-key smoke sequence lands it all in the one email field,
  which is the expected shape of typing without being able to read the page.
- **Typing into the un-driven window, in the container.** The smoke workflow
  stores an email, a password and a TOTP secret on the account, opens the
  sign-in window, and posts `email, tab, password, code, enter` to
  `/api/accounts/{id}/type`; every one returned 204, and the VNC capture
  taken right after shows `smoke@example.com` sitting in Epic's search box -
  the field that happened to have focus - with the page full screen and no
  bars, the infobar gone by way of the `CommandLineFlagSecurityWarningsEnabled`
  policy. So xdotool reaches Chrome through the X server and the keystrokes
  land in the page like anybody's.
- **The screen view end to end**, with Trove run as if in a container
  (`IN_CONTAINER=true VNC_ADDRESS=127.0.0.1:5999 DISPLAY=:0`), a minimal RFB
  3.8 server on 5999 and real Chrome driving the built interface: "Sign in
  here" opened the un-driven window, the dialog opened on its own because
  `via` was `screen`, noVNC negotiated (version, security None, ServerInit,
  SetEncodings), the raw frame drew on the canvas, a click arrived as
  `PointerEvent mask=1` at the scaled coordinates, keys arrived as keysyms
  (`0x61`, Enter `0xff0d`), "Done, close the window" returned 204, the window
  process ended, and the session check ran after it. The fake server is
  `fake_vnc.py` in the session scratchpad; it is eighty lines and worth
  recreating if the screen view is touched.
- That the Error-stack-getter CDP tell does not fire on Chrome 151 even with
  `Runtime.enable` sent, for every console method.
- That `document.hasFocus()` is **true** for a headed Chrome under Xvfb with
  no window manager at all, measured by the probe in the container without
  any focus emulation. So the container case `live.enable_focus` was kept for
  is also not a case where it changes anything observed.

**The checkout, as of Aug 2026 (from a real signed-in account):** the
`/purchase?offers=1-<ns>-<id>` URL reaches the checkout overlay in the driven
browser, and for a *free* game it reads "This is free. Add it to your library
to get started." with a single **"Add to library"** button - the age and EULA
consent folded into that click, no separate agreement step. So `PLACE_ORDER`
now leads with `Add to library`; `CONFIRMED` was broadened; and after the click
the claim falls back to checking the product page for ownership as ground
truth, because the confirmation wording is unknown and a missing banner is not
proof of failure. Seen but not on the free game: a "not compatible with your
device" notice with a "Continue" (the store's own "Get" flow shows it), stepped
past best-effort by `COMPAT_CONTINUE`. Verified from a watched run's screenshot;
the click-through in the *driven* browser then hit the captcha wall below.

**The driven click was tried, and Epic's captcha refused it (0.1.19).** "Add to
library" was clicked, Talon raised a captcha, a person solved it on the screen,
and `confirm-order` returned `400 epic.error.captcha.challenge.failed` with the
token attached. So the driven-browser checkout is settled: it reaches the order
and cannot complete it, and the remedy is the un-driven "Finish the claim here"
window. See the two paragraphs on it above.

**Written and NOT verified:**

- **The un-driven claim, end to end.** The button, the window on the checkout
  page, and `verify_checkout` are built and the sim covers the branch, but a
  real account pressing "Add to library" in the un-driven window and Trove then
  reading the game in the library has not been reported. This is the last
  unproven step of the whole claim, and the un-driven window is the same one
  that already passes sign-in, so the odds are good - but it is unproven.
- **The confirmation wording / ownership fallback.** `verify_checkout` trusts
  `is_owned` (the product page's "In Library" marker) as ground truth after the
  window closes; `CONFIRMED` in the driven flow is now moot for Epic, since the
  driven flow stops at the captcha. Whether `is_owned` reliably flips to True
  right after a hand-finished order is unproven and is the first thing to check.
- **Whether headed actually beats headless** against Epic's detection. CLAUDE.md
  asked for this to be measured and it has not been. Headed is the default as
  the conservative guess. Measuring it is a good early task and needs a signed-in
  account to be meaningful.
- **A claim run, or a sign-in, inside the container.** The smoke workflow
  proves the container starts and the browser is whole; it cannot sign in to
  a store, so whether Epic lets the container's un-driven window through, and
  whether a scheduled run from a container address is challenged, are still
  open and depend heavily on the address. The first real deployment answers
  them; `python -m app.diagnose` and the account's screenshots are the
  evidence to keep.
- **That the screen view keeps up with a real x11vnc and a real Chrome on
  it.** It has been proven end to end against a fake RFB server (below) - the
  dialog opens, the frame draws, pointer and key events arrive - but not yet
  against the container's own display with a page moving on it. The first
  person to do so should note here whether the picture keeps up and whether
  keyboard layout survives: noVNC sends X keysyms from `keydown`, so an umlaut
  typed on a real keyboard is the test again. (Playwright's `keyboard.type` is
  *not* that test: it delivers non-ASCII via `insertText`, which has no
  keydown, so the fake server saw `a` and Enter and never the `Ä`.)
- **That the mouse fix makes a Turnstile checkbox pass.** It does not, on its
  own: the checkbox still looped after it, and the codec fingerprint was the
  real cause. The defect it fixed was real - every pointer move was arriving as
  a left-button drag - and the corrected events are proven, so it stays. It is
  simply not the thing that unblocked the challenge, and the two should not be
  confused in a commit message.
- **That the live view can ever pass an interactive challenge.** It could not
  on the bundled Chromium and there is no reason to think it can on Chrome; the
  un-driven window exists because of that. If a future session wants to claim
  otherwise it needs a screenshot of a ticked checkbox, not an argument.
- **Focus emulation.** `Emulation.setFocusEmulationEnabled` is called for both
  the live view and runs, and on headed Chromium on Windows it changes nothing
  measurable: `document.hasFocus()` was already `true` with the window
  backgrounded and minimised. It is kept for the container case, which has not
  been measured. It is not the reason anything works.

## Things that will look like shortcuts and are not

- Storing store passwords and logging in on every run. It is the thing bot
  detection is looking for, and it is what turns a missed claim into a locked
  account. (The stored sign-in details of 0.1.7 are not this: they are typed
  at the person's request into their own sign-in window, and nothing that
  runs unattended can read them. Keep it that way.)
- Running the browser headless because it is easier in Docker. Measure it before
  relying on it.
- Retrying a failed claim in a loop. One attempt, then the attention queue.
- Treating "already owned" as a failure. It is the normal steady state, and the
  UI should say so quietly rather than in `critical`.
- Colouring a status with the accent, or filling a selected row with it. §2.4 is
  a closed list and this app has more status than most.
- Stating the version anywhere but `backend/app/__init__.py`. The sidebar used
  to carry `const VERSION = '0.1.0'` of its own, so upgrading the backend to
  0.1.4 left the interface reporting 0.1.0 and a correct upgrade looked like a
  failed one. It reads `/api/health` now, which is the build actually serving
  the page - so a stale cached bundle reports the *server's* version rather
  than its own. `frontend/package.json` no longer carries a version field at
  all. The release workflow checks the one remaining number against the git tag.
- Letting a naive datetime out of the API. SQLite returns every stored datetime
  without an offset, and `new Date()` reads an offset-less string as *local*
  time, so a run that just finished read as "2 hours ago" on a UTC+2 machine.
  `schemas.Read` stamps UTC on the way out; new read models inherit it.
- Returning a stored secret to the browser "because the form needs it". The
  webhook comes back as `__set__` and sending that back means "leave it alone".
