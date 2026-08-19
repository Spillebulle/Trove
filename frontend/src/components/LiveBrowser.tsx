/*
 * The account's own browser, in the page.
 *
 * This is where a person signs in for the first time and where they answer a
 * captcha a run stopped on. The backend streams the real Chromium page as JPEG
 * frames over a WebSocket and accepts input back; see `backend/app/live.py` for
 * the half that talks to Chromium.
 *
 * Three things here are worth knowing before changing them.
 *
 * **The canvas is the input surface, and coordinates are scaled.** The remote
 * viewport is fixed and the server states it once on connect, so a click at
 * canvas (x, y) maps to remote (x * remoteWidth / canvasWidth, ...). Reading
 * the size per frame instead would give a second answer that sometimes
 * disagrees, and a click that lands a few pixels off a button is worse than one
 * that lands nowhere.
 *
 * **Text is sent as text, not as key events.** A `keydown` carries a key name
 * and the browser's own layout handling; reproducing that faithfully means
 * reproducing dead keys, AltGr, phone keyboards and paste. So printable input
 * is taken from `beforeinput` on a hidden field and sent whole, and only the
 * keys that have no text - Enter, Tab, Backspace, the arrows - go through as
 * keys. A password with an umlaut in it works, which is the test that matters.
 *
 * **Frames are drawn, never accumulated.** Each arrives as a base64 JPEG and is
 * painted onto the canvas as it lands. The server drops frames the socket
 * cannot keep up with, so what is on screen is always the newest thing the
 * server had, and there is no buffer to fall behind in.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { Loader, RotateCw } from 'lucide-react'
import { liveSocketUrl } from '@/lib/api'
import { cn } from '@/lib/utils'
import { Spinner } from './ui'

type Phase = 'connecting' | 'live' | 'closed' | 'error'

// The keys that carry no text and so cannot go through `beforeinput`. Kept in
// step with CONTROL_KEYS in `backend/app/live.py`; a key missing from either
// side simply does nothing, which is why both lists are short and explicit.
const CONTROL_KEYS = new Set([
  'Backspace',
  'Tab',
  'Enter',
  'Escape',
  'PageUp',
  'PageDown',
  'End',
  'Home',
  'ArrowLeft',
  'ArrowUp',
  'ArrowRight',
  'ArrowDown',
  'Delete',
])

/** CDP's modifier bitmask: Alt 1, Ctrl 2, Meta 4, Shift 8. */
function modifiersOf(event: { altKey: boolean; ctrlKey: boolean; metaKey: boolean; shiftKey: boolean }) {
  return (
    (event.altKey ? 1 : 0) |
    (event.ctrlKey ? 2 : 0) |
    (event.metaKey ? 4 : 0) |
    (event.shiftKey ? 8 : 0)
  )
}

export function LiveBrowser({
  accountId,
  onClose,
}: {
  accountId: number
  onClose: () => void
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const socketRef = useRef<WebSocket | null>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  // The remote viewport, from the server's `ready` message. A ref rather than
  // state because every pointer event reads it and none of them should render.
  const remoteRef = useRef({ width: 1280, height: 800 })

  const [phase, setPhase] = useState<Phase>('connecting')
  const [message, setMessage] = useState<string | null>(null)
  const [url, setUrl] = useState('')

  const send = useCallback((payload: object) => {
    const socket = socketRef.current
    if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(payload))
  }, [])

  /* ── The socket ───────────────────────────────────────────────────────── */

  useEffect(() => {
    const socket = new WebSocket(liveSocketUrl(accountId))
    socketRef.current = socket

    // The decoder is reused across frames. A new Image per frame is a new
    // decode job per frame and, at a dozen a second, enough garbage to show as
    // a stutter in the stream it is meant to be drawing.
    const image = new Image()
    let pending: string | null = null
    let drawing = false

    const draw = () => {
      if (pending === null || drawing) return
      drawing = true
      const data = pending
      pending = null
      image.onload = () => {
        const canvas = canvasRef.current
        const context = canvas?.getContext('2d')
        if (canvas && context) {
          canvas.width = remoteRef.current.width
          canvas.height = remoteRef.current.height
          context.drawImage(image, 0, 0, canvas.width, canvas.height)
        }
        drawing = false
        draw()
      }
      image.onerror = () => {
        drawing = false
        draw()
      }
      image.src = `data:image/jpeg;base64,${data}`
    }

    socket.onmessage = (event) => {
      const payload = JSON.parse(event.data as string)
      switch (payload.type) {
        case 'ready':
          remoteRef.current = { width: payload.width, height: payload.height }
          setUrl(payload.url ?? '')
          setPhase('live')
          break
        case 'frame':
          // Keep only the newest: an older frame that has not been drawn is a
          // frame nobody needs to see any more.
          pending = payload.data
          draw()
          break
        case 'status':
        case 'pong':
          if (payload.url) setUrl(payload.url)
          break
        case 'error':
          setMessage(payload.message)
          setPhase('error')
          break
      }
    }

    socket.onerror = () => {
      setMessage('The live view lost its connection to Trove.')
      setPhase('error')
    }

    socket.onclose = () => {
      setPhase((current) => (current === 'error' ? current : 'closed'))
    }

    // A keepalive, so a proxy that closes idle sockets does not take the window
    // down while somebody is reading a captcha. Deliberately not counted as
    // activity by the server: a window left open in a background tab should
    // still time out and give the profile back.
    const keepalive = window.setInterval(() => send({ type: 'ping' }), 25_000)

    return () => {
      window.clearInterval(keepalive)
      if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: 'done' }))
      socket.close()
      socketRef.current = null
    }
  }, [accountId, send])

  /* ── Pointer ──────────────────────────────────────────────────────────── */

  const toRemote = useCallback((event: React.PointerEvent | React.WheelEvent) => {
    const canvas = canvasRef.current
    if (!canvas) return { x: 0, y: 0 }
    const box = canvas.getBoundingClientRect()
    return {
      x: ((event.clientX - box.left) / box.width) * remoteRef.current.width,
      y: ((event.clientY - box.top) / box.height) * remoteRef.current.height,
    }
  }, [])

  const onPointer = (kind: 'mousePressed' | 'mouseReleased' | 'mouseMoved') =>
    (event: React.PointerEvent) => {
      const { x, y } = toRemote(event)
      send({
        type: 'mouse',
        event: kind,
        x,
        y,
        // `button` is which button *changed* and is -1 on a plain move;
        // `buttons` is the bitmask of what is held right now. Both are needed:
        // with only the first, the server cannot tell a hover from a drag, and
        // it used to resolve every move to "left button held" - which turned
        // moving the pointer across the page into one long drag gesture.
        button: event.button,
        buttons: event.buttons,
        clickCount: kind === 'mouseMoved' ? 0 : 1,
        modifiers: modifiersOf(event),
      })
      // A click has to put the keyboard somewhere. The hidden field is the only
      // thing on the page that can hold it, and without this every keystroke
      // after a click on the canvas goes to the page behind it.
      //
      // `preventScroll` is not optional. Focusing an element scrolls it into
      // view, and this one used to sit at `left: -9999px`, so a press could
      // scroll its own container sideways - moving the canvas out from under
      // the pointer between `pointerdown` and `pointerup`, and landing the
      // release somewhere the user never clicked.
      if (kind === 'mousePressed') inputRef.current?.focus({ preventScroll: true })
    }

  /* ── Keyboard ─────────────────────────────────────────────────────────── */

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (!CONTROL_KEYS.has(event.key)) return
    // Tab would otherwise walk the app's own controls while the user is
    // tabbing between fields on a store's sign-in form.
    event.preventDefault()
    send({ type: 'key', key: event.key, modifiers: modifiersOf(event) })
  }

  const onBeforeInput = (event: React.FormEvent<HTMLTextAreaElement>) => {
    const data = (event.nativeEvent as InputEvent).data
    event.preventDefault()
    if (data) send({ type: 'text', text: data })
  }

  const onPaste = (event: React.ClipboardEvent) => {
    event.preventDefault()
    const text = event.clipboardData.getData('text')
    if (text) send({ type: 'text', text })
  }

  /* ── Chrome ───────────────────────────────────────────────────────────── */

  return (
    <div className="flex h-full min-h-0 flex-col gap-2">
      <div className="flex items-center gap-2">
        {/* The address is shown and not typed. This window exists to sign in to
            one store, and a bar that accepts a URL is a browser inside an
            authenticated app. The server refuses off-store navigation anyway;
            not drawing the control is the honest version of that. */}
        <span
          className="chip min-w-0 flex-1 truncate font-mono"
          title={url || 'Waiting for the page.'}
        >
          {url || 'Waiting for the page.'}
        </span>
        <button
          type="button"
          className="btn-icon"
          title="Reload the page."
          aria-label="Reload the page."
          onClick={() => send({ type: 'reload' })}
          disabled={phase !== 'live'}
        >
          <RotateCw className="size-icon" />
        </button>
        <button type="button" className="btn-secondary" onClick={onClose}>
          Done
        </button>
      </div>

      <div className="relative flex min-h-0 flex-1 items-center justify-center overflow-hidden rounded-tool border border-line bg-window">
        <canvas
          ref={canvasRef}
          /*
           * `max-h-full max-w-full`, never `h-full w-full object-contain`.
           *
           * A canvas is a replaced element, so `object-contain` letterboxes the
           * 1280x800 bitmap *inside* the element box rather than resizing the
           * box. `toRemote` maps a click through `getBoundingClientRect()`,
           * which is the element - bars included - so every coordinate was
           * offset by the bar and scaled by the wrong ratio. It read as "clicks
           * work sometimes", because how wrong it is depends on the container's
           * aspect ratio and on where in the frame you press.
           *
           * With `max-*` and no explicit size, the browser scales the canvas by
           * its intrinsic 1280x800, so the element box *is* the drawn area and
           * the mapping is exact. The flex parent centres what is left.
           */
          className={cn(
            // `touch-none` so a drag on a touch screen scrolls the remote page
            // rather than the app around it.
            'max-h-full max-w-full touch-none',
            phase !== 'live' && 'opacity-40',
          )}
          onPointerDown={onPointer('mousePressed')}
          onPointerUp={onPointer('mouseReleased')}
          onPointerMove={onPointer('mouseMoved')}
          onWheel={(event) => {
            const { x, y } = toRemote(event)
            send({
              type: 'mouse',
              event: 'mouseWheel',
              x,
              y,
              deltaX: event.deltaX,
              deltaY: event.deltaY,
              buttons: event.buttons,
              modifiers: modifiersOf(event),
            })
          }}
          onContextMenu={(event) => event.preventDefault()}
        />

        {/*
         * The keyboard's home. Off screen rather than `display: none`, because
         * a hidden element cannot take focus and an element that cannot take
         * focus receives no keys. It is never read from: `beforeinput` is
         * cancelled, so it stays empty however much is typed into it.
         */}
        <textarea
          ref={inputRef}
          // Inside the viewport rather than parked off-screen, so focusing it
          // has nowhere to scroll to in the first place; `pointer-events-none`
          // keeps a 1px invisible field from ever swallowing a click, while
          // still allowing `focus()` to reach it.
          className="pointer-events-none absolute left-0 top-0 h-px w-px opacity-0"
          aria-label="Keyboard input for the live browser."
          onKeyDown={onKeyDown}
          onBeforeInput={onBeforeInput}
          onPaste={onPaste}
          autoComplete="off"
          spellCheck={false}
        />

        {phase === 'connecting' && (
          <div className="absolute inset-0 grid place-items-center">
            <span className="flex items-center gap-2 text-body text-dim">
              <Spinner />
              Opening the browser for this account.
            </span>
          </div>
        )}

        {(phase === 'error' || phase === 'closed') && (
          <div className="absolute inset-0 grid place-items-center px-6">
            <div className="max-w-sm text-center">
              <p className="text-body text-fg">
                {message ??
                  (phase === 'closed'
                    ? 'The live view has closed. The browser profile is free again.'
                    : 'Something went wrong.')}
              </p>
              <button type="button" className="btn-secondary mt-3" onClick={onClose}>
                Close
              </button>
            </div>
          </div>
        )}
      </div>

      <p className="text-small text-dim">
        This is the real browser for this account. Sign in, or answer whatever
        the store is asking, then press Done. Trove keeps the session and never
        sees your password.
      </p>
    </div>
  )
}

export function LiveBrowserFallback() {
  return (
    <div className="grid h-full place-items-center">
      <Loader className="size-icon animate-spin text-dim" />
    </div>
  )
}
