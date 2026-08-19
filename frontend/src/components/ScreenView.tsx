/*
 * Trove's own screen, in the page.
 *
 * In a container the browsers draw on an Xvfb display nobody stands in front
 * of. This shows that display - the whole of it, through a VNC server the
 * entrypoint runs beside it - and takes the mouse and keyboard back. It is what
 * makes "sign in here" work in a container: the account's Chrome is opened
 * there *un-driven*, with no DevTools protocol and no automation flags on it,
 * and the person works it from here.
 *
 * It is not the live view and it must not become one. The live view is a
 * screencast of one tab over CDP, and a page can tell CDP is attached; that is
 * why a challenge refuses it. This reads pixels off the X server. The browser
 * on the other side has nothing attached to it at all, which is the entire
 * point and the reason it is worth a second viewer.
 *
 * The heavy lifting is noVNC's RFB client. The socket is Trove's own
 * (`/api/screen`), authenticated by the same cookie as everything else and
 * bridged to the VNC port inside the container, so nothing but a signed-in
 * Trove user can ever see a store session.
 */
import { useEffect, useRef, useState } from 'react'
import { ClipboardPaste } from 'lucide-react'
import RFB from '@novnc/novnc'
import { screenSocketUrl } from '@/lib/api'
import { useToast } from '@/lib/app-context'
import { Spinner } from './ui'

type Phase = 'connecting' | 'live' | 'closed' | 'error'

export function ScreenView({
  onClose,
  footer,
}: {
  onClose: () => void
  footer?: React.ReactNode
}) {
  const hostRef = useRef<HTMLDivElement>(null)
  const rfbRef = useRef<RFB | null>(null)
  const [phase, setPhase] = useState<Phase>('connecting')
  const [message, setMessage] = useState<string | null>(null)
  const { push } = useToast()

  // Hand the browser on the other side what is on this clipboard, so a
  // password from a manager can be pasted there with Ctrl+V rather than typed
  // key by key through a remote screen. It goes over the VNC connection as a
  // cut-text message and lands in the X selection; nothing is logged or kept.
  const pasteClipboard = async () => {
    const rfb = rfbRef.current
    if (!rfb) return
    try {
      const text = await navigator.clipboard.readText()
      if (!text) {
        push('Your clipboard is empty.', 'neutral')
        return
      }
      rfb.clipboardPasteFrom(text)
      push('Sent to the screen. Press Ctrl+V in the field over there.', 'good')
    } catch {
      push('This browser would not share the clipboard. Type it instead.', 'neutral')
    }
  }

  useEffect(() => {
    const host = hostRef.current
    if (!host) return
    let rfb: RFB
    try {
      rfb = new RFB(host, screenSocketUrl(), { shared: true })
    } catch (error) {
      setPhase('error')
      setMessage(error instanceof Error ? error.message : 'Could not open the screen.')
      return
    }
    rfbRef.current = rfb
    // The display is 1280x800 and the dialog is whatever it is; scale the
    // picture to fit and let noVNC map the pointer through the same scale.
    rfb.scaleViewport = true
    rfb.resizeSession = false
    rfb.focusOnClick = true
    rfb.viewOnly = false
    // Rendering quality over bandwidth: a sign-in page is text, and a person
    // reading a captcha wants it sharp.
    rfb.qualityLevel = 7
    rfb.compressionLevel = 3

    const onConnect = () => setPhase('live')
    const onDisconnect = (event: CustomEvent<{ clean: boolean }>) => {
      setPhase((current) => (current === 'error' ? current : 'closed'))
      if (!event.detail.clean) {
        setMessage(
          'The screen connection dropped. The browser on the other side is still there; open the screen again to carry on.',
        )
      }
    }
    rfb.addEventListener('connect', onConnect)
    rfb.addEventListener('disconnect', onDisconnect as EventListener)

    return () => {
      rfb.removeEventListener('connect', onConnect)
      rfb.removeEventListener('disconnect', onDisconnect as EventListener)
      try {
        rfb.disconnect()
      } catch {
        // Already gone.
      }
      rfbRef.current = null
    }
  }, [])

  return (
    <div className="flex h-full flex-col gap-3">
      <div className="relative min-h-0 flex-1 overflow-hidden rounded-control bg-sunk">
        {/* noVNC owns this element: it appends its own canvas and sizes it. */}
        <div ref={hostRef} className="absolute inset-0" />

        {phase === 'connecting' && (
          <div className="absolute inset-0 grid place-items-center">
            <span className="flex items-center gap-2 text-body text-dim">
              <Spinner />
              Connecting to Trove&rsquo;s screen.
            </span>
          </div>
        )}

        {(phase === 'error' || phase === 'closed') && (
          <div className="absolute inset-0 grid place-items-center px-6">
            <div className="max-w-sm text-center">
              <p className="text-body text-fg">
                {message ??
                  (phase === 'closed' ? 'The screen view has closed.' : 'Something went wrong.')}
              </p>
              <button type="button" className="btn-secondary mt-3" onClick={onClose}>
                Close
              </button>
            </div>
          </div>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <p className="min-w-0 flex-1 text-small text-dim">
          This is the screen inside the container, and the browser on it has
          nothing attached to it: no automation, no remote control, only you.
          Sign in, answer what the store asks, then close the window. Trove
          keeps the session and never sees your password.
        </p>
        <button
          type="button"
          className="btn-ghost"
          onClick={() => void pasteClipboard()}
          disabled={phase !== 'live'}
          title="Send what is on your clipboard to the screen, so you can Ctrl+V it there."
        >
          <ClipboardPaste className="size-icon" />
          Paste
        </button>
        {footer}
      </div>
    </div>
  )
}
