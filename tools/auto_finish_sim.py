"""Does `auto_finish` actually fire? Produce Epic's end-state and watch it.

The first real claim (20 Aug 2026) ended with Epic closing its own checkout
window while the Chrome process lived, which on an Xvfb with no window manager
is a black screen and nothing else. `browser.open_local(auto_finish=True)` is
the watcher written against that claim's log - `keyboard.window_titles` asks
the X server every two seconds, and once the browser has had no visible window
for `BLANK_POLLS_BEFORE_FINISH` polls running (having had one earlier, so a
slow start is never read as a finish) it closes the browser itself and runs
`on_closed` exactly as pressing Done would. CLAUDE.md's honest ledger has it at
the top of "written and NOT verified": it has never been seen to fire.

This makes it fire, inside the container, without Epic. The state the watcher
looks for is observable and simulatable: a Chrome process that is alive whose
class has no *viewable* window on the display. `xdotool windowunmap` produces
exactly that - the window is unmapped on the X server (map_state stops being
IsViewable, so `search --onlyvisible`, which is what `window_titles` runs,
stops returning it) while Chrome itself keeps running, never having been asked
anything. That is the same picture `window_titles` saw on the real claim:
process alive, titles empty. The script *verifies* both halves of that state
after the unmap rather than assuming them - if Chrome died, or the window came
back, it says so and fails; it never fakes the state.

What it asserts, in order:

1. The latch: while a window IS visible for longer than the blank threshold,
   nothing fires - Chrome stays up, `on_closed` does not run.
2. The finish: after the window goes away with the process alive, `on_closed`
   runs within `BLANK_POLLS_BEFORE_FINISH * poll + margin`, neither earlier
   (a latch that fires early is broken) nor never.
3. The teardown: the Chrome process is gone - the watcher terminated it, the
   way it would close Epic's abandoned browser.
4. The lock: the per-account profile lock is released - proven by manager
   state AND by actually opening the profile a second time, which is what a
   session check needs to do right after a finished checkout.

It polls the display once a second throughout and prints what it sees, so the
CI log reads as a narrative of the whole thing; `app.browser`'s own INFO lines
(the watcher logging each change in what is on the screen) are interleaved,
which is the watcher's view of the same events.

Runs inside the container in the smoke workflow, as pwuser, on the entrypoint's
Xvfb - the exact environment a real claim's checkout window lives in:

    docker exec -e PYTHONPATH=/app -u pwuser -w /app trove python /tmp/auto_finish_sim.py
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

# The watcher's own narration (what the screen looks like, when it decides to
# finish) is half the measurement; let it through to the log.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

from app.browser import BLANK_POLLS_BEFORE_FINISH, manager  # noqa: E402
from app.keyboard import window_titles  # noqa: E402

# open_local's `_wait` polls the display every two seconds (`asyncio.sleep(2)`
# in browser.py). Stated here once so the deadlines below say what they mean.
POLL_S = 2.0
# Everything after the blank polls: the terminate, Chrome's own shutdown, the
# next poll seeing the exit, and the deliberate 3 s wait before `on_closed`.
MARGIN_S = 30.0
# Nowhere near any real account id; the manager in this process is ours alone,
# but the profile directory is on shared /tmp and carries the id.
ACCOUNT_ID = 990_001

# A harmless page with a recognisable title, so the poll-by-poll narrative
# below and the watcher's own log lines show a real window coming and going.
URL = "data:text/html,<title>Trove%20auto-finish%20sim</title><h1>Checkout%20stand-in</h1>"

t0 = time.monotonic()


def say(text: str) -> None:
    print(f"[{time.monotonic() - t0:6.1f}s] {text}", flush=True)


def fail(text: str) -> int:
    say("FAIL: " + text)
    return 1


def _xdotool(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [shutil.which("xdotool") or "xdotool", *args], capture_output=True, timeout=10
    )


async def narrate(proc, stop: asyncio.Event) -> None:
    """Once a second: what is on the display, whether Chrome lives, who holds the lock."""
    last = None
    unchanged = 0
    while not stop.is_set():
        titles = await asyncio.to_thread(window_titles)
        alive = proc.poll() is None
        line = (
            f"chrome={'alive' if alive else f'exited({proc.returncode})'} "
            f"windows={titles!r} holder={manager.who_holds(ACCOUNT_ID)!r}"
        )
        # Every change, plus a heartbeat while nothing changes, so the log is
        # a poll-by-poll record without sixty identical lines.
        unchanged = 0 if line != last else unchanged + 1
        if line != last or unchanged % 5 == 0:
            say(line)
            last = line
        try:
            await asyncio.wait_for(stop.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            pass


async def wait_for(predicate, timeout_s: float, what: str) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if await asyncio.to_thread(predicate):
            return True
        await asyncio.sleep(0.5)
    say(f"timed out after {timeout_s:.0f}s waiting for {what}")
    return False


async def main() -> int:
    if window_titles() is None:
        return fail("no DISPLAY or no xdotool here; this only means anything in the container")

    say(
        f"BLANK_POLLS_BEFORE_FINISH={BLANK_POLLS_BEFORE_FINISH}, poll={POLL_S:.0f}s "
        f"-> blank threshold {BLANK_POLLS_BEFORE_FINISH * POLL_S:.0f}s, "
        f"finish deadline {BLANK_POLLS_BEFORE_FINISH * POLL_S + MARGIN_S:.0f}s"
    )

    # The display must be ours: window_titles counts every chrome-class window
    # on the screen, so a browser left over from another step would stop the
    # blank state from ever being reached and the measurement would be of the
    # wrong thing.
    if not await wait_for(lambda: window_titles() == [], 30, "an empty display"):
        return fail(f"the display already has chrome windows on it: {window_titles()!r}")

    profile = Path(tempfile.mkdtemp(prefix="auto-finish-sim-"))
    marker = profile / "on-closed-ran"
    closed_at: list[float] = []

    async def on_closed() -> None:
        closed_at.append(time.monotonic())
        marker.touch()
        say("on_closed ran (the verification a real claim would run now)")

    try:
        say("opening the un-driven window with auto_finish=True, as finish-claim does")
        await manager.open_local(
            ACCOUNT_ID, profile / "profile", URL, on_closed=on_closed, auto_finish=True
        )
        entry = manager._accounts[ACCOUNT_ID]
        proc = entry.process
        assert proc is not None

        stop = asyncio.Event()
        narrator = asyncio.create_task(narrate(proc, stop))
        try:
            # ── 1. A window appears, and while it is up nothing fires ────────
            if not await wait_for(lambda: bool(window_titles()), 60, "a visible window"):
                return fail("Chrome never put a visible window on the display")
            say(f"window up: {window_titles()!r}")

            hold = BLANK_POLLS_BEFORE_FINISH * POLL_S + 2 * POLL_S
            say(f"holding {hold:.0f}s with the window visible: the latch must not fire")
            await asyncio.sleep(hold)
            if marker.exists():
                return fail(
                    "DEFECT: on_closed ran while a window was visible - the "
                    "blank-poll latch fired with something on the screen"
                )
            if proc.poll() is not None:
                return fail("Chrome exited on its own during the visible hold")
            say("held: window visible, chrome alive, on_closed has not run. Correct.")

            # ── 2. Epic's move: the window goes, the process stays ───────────
            found = _xdotool("search", "--onlyvisible", "--class", "chrome")
            ids = found.stdout.decode(errors="replace").split()
            say(f"unmapping {len(ids)} visible chrome window(s): {ids}")
            for window_id in ids:
                unmapped = _xdotool("windowunmap", window_id)
                if unmapped.returncode != 0:
                    say(f"windowunmap {window_id}: {unmapped.stderr.decode(errors='replace').strip()}")
            unmapped_at = time.monotonic()

            # Verify the simulated state IS the real claim's state before
            # trusting anything built on it. Do not fake it: if Chrome died,
            # or the window came back, this approach does not produce
            # "process alive, no visible window" and the run must say so.
            await asyncio.sleep(1)
            titles_now = window_titles()
            if proc.poll() is not None:
                return fail(
                    "windowunmap took the whole process down, so this approach "
                    "cannot produce 'process alive, window gone'; a different "
                    "simulation is needed and this run proves nothing"
                )
            if titles_now:
                return fail(
                    f"the unmapped window is still visible to window_titles: "
                    f"{titles_now!r} - either the unmap failed or "
                    f"--onlyvisible does not exclude unmapped windows"
                )
            say("state verified: chrome alive, window_titles []. The watcher should now count.")

            # ── 3. The finish ────────────────────────────────────────────────
            deadline = BLANK_POLLS_BEFORE_FINISH * POLL_S + MARGIN_S
            if not await wait_for(marker.exists, deadline, "on_closed"):
                return fail(
                    f"auto_finish did not fire within {deadline:.0f}s of the "
                    f"window going away; the watcher's own log above says what "
                    f"it saw instead. windows now: {window_titles()!r}, "
                    f"chrome alive: {proc.poll() is None}"
                )
            took = closed_at[0] - unmapped_at
            say(f"on_closed ran {took:.1f}s after the window went away")
            floor = (BLANK_POLLS_BEFORE_FINISH - 1) * POLL_S
            if took < floor:
                return fail(
                    f"DEFECT: the finish fired after {took:.1f}s, under the "
                    f"{floor:.0f}s the blank-poll count should enforce - the "
                    f"latch is not counting polls"
                )
            if proc.poll() is None:
                # on_closed only runs after the waiter saw the process exit,
                # so this would mean the waiter is watching the wrong process.
                return fail("DEFECT: on_closed ran but the Chrome process is still alive")
            say(f"chrome exited ({proc.returncode}): the watcher closed the abandoned browser")

            # ── 4. The lock ──────────────────────────────────────────────────
            holder = manager.who_holds(ACCOUNT_ID)
            if holder is not None or entry.lock.locked():
                return fail(
                    f"DEFECT: the profile lock was not released after the finish "
                    f"(holder={holder!r}, locked={entry.lock.locked()})"
                )
            say("manager state: no holder, lock free")

            say("opening the same profile a second time, as a session check would")
            await manager.open_local(ACCOUNT_ID, profile / "profile", URL)
            proc2 = entry.process
            assert proc2 is not None
            if not await wait_for(lambda: bool(window_titles()), 60, "the second window"):
                return fail("the second open_local put no window up")
            say(f"second window up: {window_titles()!r}")
            # Past open_local's "exited within seconds" heuristic, so closing
            # it does not log a misleading "Chrome did not come up" warning.
            await asyncio.sleep(5)
            say("closing it via close_local")
            manager.close_local(ACCOUNT_ID)
            if not await wait_for(
                lambda: proc2.poll() is not None and not entry.lock.locked(), 30,
                "the second window to close and release the lock",
            ):
                return fail("the second window did not close cleanly")
        finally:
            stop.set()
            await narrator

        say("")
        say("MEASURED: latch held through a visible window; with the process "
            "alive and the window gone, auto_finish closed the browser, ran "
            "on_closed, and released the lock; the profile could be reopened.")
        return 0
    finally:
        # Belt and braces: never leave a Chrome on the shared display for a
        # later workflow step to trip over (the sign-in step greps for the
        # very window flags this one carries).
        entry = manager._accounts.get(ACCOUNT_ID)
        leftover = entry.process if entry else None
        if leftover is not None and leftover.poll() is None:
            leftover.terminate()
            try:
                await asyncio.to_thread(leftover.wait, 10)
            except subprocess.TimeoutExpired:
                leftover.kill()
        await manager.stop()
        shutil.rmtree(profile, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
