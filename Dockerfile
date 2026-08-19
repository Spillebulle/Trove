# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Stage 1 - build the React frontend
# ---------------------------------------------------------------------------
# Pinned to BUILDPLATFORM: the output is static JS and CSS with no architecture
# of its own, so building it natively rather than under QEMU keeps the arm64
# image fast to produce and stops npm resolving platform-specific optional
# dependencies (esbuild, rollup) for an emulated target.
FROM --platform=$BUILDPLATFORM node:22-alpine AS frontend

WORKDIR /build

# Manifests first, so the dependency layer is cached across source edits.
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --no-audit --no-fund

COPY frontend/ ./
RUN npm run build

# ---------------------------------------------------------------------------
# Stage 2 - runtime
# ---------------------------------------------------------------------------
# Microsoft's Playwright image rather than python:slim plus `playwright install`.
# Chromium needs about forty shared libraries, the list changes between browser
# releases, and getting it wrong presents as a browser that launches and dies
# with no message. This image is built by the people who pin that list, and its
# tag matches the `playwright` pin in requirements.txt exactly - the Python
# package and the bundled browser have to agree, or Playwright refuses to start
# with a version error.
#
# It is a large base (about 1.8 GB). That is the price of driving a real
# browser, and the alternative is an image that is smaller and does not work.
FROM mcr.microsoft.com/playwright/python:v1.49.1-noble AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATA_DIR=/data \
    HOST=0.0.0.0 \
    PORT=8080 \
    # The display the entrypoint's Xvfb puts up. Headed Chromium needs one, and
    # headed is the default for the reason in backend/app/browser.py.
    DISPLAY=:99 \
    # Tells Trove nobody can see that display. Without it the app would offer
    # the "sign in here" button - DISPLAY is set, after all - and open a window
    # onto a framebuffer the user has no way to reach.
    IN_CONTAINER=true

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl tini xvfb gosu \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Real Google Chrome, on top of the Chromium the base image already carries.
#
# This is not belt and braces, it is the difference between a store letting you
# past a captcha and refusing you forever. Playwright's bundled Chromium ships
# **no proprietary codecs**: measured side by side, it answers "no" to H.264,
# HEVC and AAC where Chrome answers "probably", and its `userAgentData.brands`
# never says "Google Chrome" - while its user-agent string claims to be Chrome.
# Cloudflare's Turnstile probes for exactly those codecs, so the bundle fails a
# challenge no matter how honestly a person answers it. `browser_channel=auto`
# picks this up automatically; without it Trove falls back to the bundle and
# says so loudly in the log.
RUN playwright install --with-deps chrome \
    && rm -rf /var/lib/apt/lists/*

# The base image ships browsers for the `pwuser` it creates. Trove runs as that
# user so it can read them, and the entrypoint remaps its ids to PUID/PGID at
# startup so it can also write to a bind-mounted /data owned by the host user.
WORKDIR /app
COPY backend/app ./app
COPY --from=frontend /build/dist ./app/static
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

RUN chmod +x /usr/local/bin/docker-entrypoint.sh \
    && mkdir -p /data \
    && chown -R pwuser:pwuser /data /app

VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/api/health" || exit 1

# tini reaps what Chromium leaves behind. A browser that is killed mid-run
# leaves zombie renderer processes, and a container that accumulates them runs
# out of pids after a few weeks of claiming.
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/docker-entrypoint.sh"]
CMD ["sh", "-c", "exec uvicorn app.main:app --host ${HOST} --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
