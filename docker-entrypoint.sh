#!/bin/sh
set -e

# Trove's entrypoint does two things before handing over to uvicorn: it puts up
# a virtual display, and it makes the container's user match whoever owns the
# mounted volume.

# ── The display ──────────────────────────────────────────────────────────────
#
# Headed Chromium needs an X display, and headed is the default because
# `headless=new` is one of the signals bot detection reads (see
# backend/app/browser.py). Xvfb is that display and nothing else is drawn on
# it: nobody ever looks at this screen, the live view reads the page over the
# DevTools protocol rather than off the framebuffer. Set TROVE_HEADLESS=true to
# skip it and run headless instead.
if [ "${TROVE_HEADLESS}" != "true" ]; then
  # Readiness is the X socket appearing, not `xdpyinfo`.
  #
  # This used to call `xdpyinfo`, which lives in `x11-utils` and is **not**
  # installed by `xvfb`. A missing command returns non-zero, so the wait loop
  # could never succeed and the entrypoint exited 1 after ten seconds: the
  # container never started at all. Testing for the socket the X server itself
  # creates needs no package and cannot rot the same way.
  socket="/tmp/.X11-unix/X${DISPLAY#:}"
  if [ ! -e "${socket}" ]; then
    # 1280x800 matches the viewport in browser.py. A display smaller than the
    # viewport makes Chromium letterbox the page, which shows up in the live
    # view as a window with black bars nobody can explain.
    Xvfb "${DISPLAY}" -screen 0 1280x800x24 -nolisten tcp >/dev/null 2>&1 &
    xvfb_pid=$!
    # Wait for it rather than sleeping a fixed second: on a slow host the
    # browser used to start before the display existed and die with
    # "Missing X server", which reads as a Playwright bug and is not one.
    i=0
    while [ ! -e "${socket}" ]; do
      # If Xvfb itself died, say that rather than timing out with no reason.
      if ! kill -0 "${xvfb_pid}" 2>/dev/null; then
        echo "Xvfb exited immediately. Is the xvfb package present?" >&2
        exit 1
      fi
      i=$((i + 1))
      if [ "$i" -gt 100 ]; then
        echo "Xvfb did not come up on ${DISPLAY} after ten seconds." >&2
        exit 1
      fi
      sleep 0.1
    done
  fi
fi

# ── The user ─────────────────────────────────────────────────────────────────
#
# A bind-mounted ./data is owned by the host user, and the container's pwuser
# is 1000 only by luck. PUID/PGID remap it so the database and the browser
# profiles are writable without anybody chmod-ing 777.
PUID=${PUID:-1000}
PGID=${PGID:-1000}

if [ "$(id -u)" = "0" ]; then
  CURRENT_UID=$(id -u pwuser)
  CURRENT_GID=$(id -g pwuser)
  if [ "${PGID}" != "${CURRENT_GID}" ]; then
    groupmod -o -g "${PGID}" pwuser
  fi
  if [ "${PUID}" != "${CURRENT_UID}" ]; then
    usermod -o -u "${PUID}" pwuser
  fi
  # /data only. Not /app: the browsers under /ms-playwright are hundreds of
  # megabytes of read-only files, and chown-ing them on every start added the
  # better part of a minute to boot for no gain.
  chown -R pwuser:pwuser /data 2>/dev/null || true
  exec gosu pwuser "$@"
fi

exec "$@"
