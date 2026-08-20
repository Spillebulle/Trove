![Trove](https://raw.githubusercontent.com/Spillebulle/Trove/main/docs/images/banner.png)

# Trove

Claims the games that are temporarily free on stores you already have accounts
with, starting with the Epic Games Store. Self-hosted, one browser profile per
account, a ledger of every attempt, and Discord or webhook notifications.

Source and full documentation: **https://github.com/Spillebulle/Trove**

## Run it

```yaml
services:
  trove:
    image: spillebulle/trove:latest
    container_name: trove
    restart: unless-stopped
    ports:
      - "8080:8080"
    volumes:
      - ./data:/data
    environment:
      - ADMIN_PASSWORD=pick-something
      - PUID=1000
      - PGID=1000
      - TZ=Europe/Oslo
    shm_size: 1gb
```

Then open `http://localhost:8080` and sign in as `admin`.

`shm_size: 1gb` is not optional. The image runs a real Chrome, and below about
512 MB of `/dev/shm` it crashes part-way through a store page, which looks like
a claim failing for no reason on some games and not others.

**`linux/amd64` only.** The image installs real Google Chrome, and Google ships
no Chrome for Linux on arm64. Read on for why that matters.

## What it does

Trove signs in to your own store accounts on your own machine, on a schedule,
and claims what is being given away. It keeps a browser profile per account
rather than a password, so a healthy account signs in once by hand and never
again. There is no password field anywhere in the app.

Finding out what is free costs one request to a public endpoint and touches no
account, so the browser only wakes up when there is something to claim. When a
store asks a question Trove cannot answer, it stops, screenshots what it saw,
marks the account, and tells you. It does not retry.

## The one thing to know before you start

**The container cannot do the first sign-in.** It has no screen you can see, so
the "sign in here" button is refused there, and an interactive Cloudflare
challenge can refuse the streamed live view too.

Sign in on a desktop machine running Trove, then copy that account's folder:

```
data/profiles/<id>-<slug>/
```

into the container's volume at the same path, and press **Check again** on the
account. Trove asks the store whether the session is good rather than assuming,
so a profile that did not survive the trip says so.

Everything after that - the schedule, claiming, the ledger, notifications -
runs in the container normally.

## Configuration

| Variable | Default | What it is |
|---|---|---|
| `ADMIN_PASSWORD` | `changeme` | The password for Trove itself. Set it before the first start. |
| `PUID` / `PGID` | `1000` | Match the owner of your `./data` directory. |
| `DATA_DIR` | `/data` | Database, browser profiles, screenshots, generated keys. |
| `DEFAULT_INTERVAL_HOURS` | `8` | How often an account is checked, unless it sets its own. |
| `BROWSER_CHANNEL` | `auto` | Uses the real Chrome in the image. Leave it alone. |
| `TZ` | `Europe/Oslo` | |

Back up `./data`. It holds the database, the encryption keys and your
signed-in browser sessions; lose it and every account signs in by hand again.

## Status

Early. One store works end to end, the interface is complete, and the sign-in
flow has been proven against the live store. Epic's checkout is written but has
never run against a signed-in account, and no container has yet been started
from this image in anger. The repository's `CLAUDE.md` keeps an honest list
under "What is verified and what is not".

## Licence

GPL-3.0. Trove automates logins to stores you already have accounts with, for
your own accounts, on your own machine. That can breach a store's terms of
service, and it is your call to make.
