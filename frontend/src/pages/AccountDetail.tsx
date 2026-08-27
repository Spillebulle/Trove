/*
 * One account: its state, the live view, its settings, and its own history.
 *
 * This is the page the whole app funnels to when something needs a person, so
 * the attention notice is at the top with the two things that answer it side by
 * side: open the browser, and say it is fixed. Everything else is below.
 */
import { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AtSign,
  Check,
  ClipboardPaste,
  CornerDownLeft,
  ExternalLink,
  Eye,
  Hash,
  KeyRound,
  LogIn,
  Monitor,
  Play,
  RefreshCw,
  Trash2,
} from 'lucide-react'
import { api, type SignInForm } from '@/lib/api'
import { useToast } from '@/lib/app-context'
import {
  ACCOUNT_STATUS_LABEL,
  ACCOUNT_STATUS_TONE,
  OUTCOME_LABEL,
  OUTCOME_TONE,
  RUN_STATUS_LABEL,
  RUN_STATUS_TONE,
  duration,
  everyHours,
  fullTime,
  relativeTime,
} from '@/lib/utils'
import { LiveBrowser } from '@/components/LiveBrowser'
import { ScreenView } from '@/components/ScreenView'
import {
  ActionMenu,
  Art,
  ConfirmDialog,
  Dialog,
  EmptyState,
  ErrorState,
  Field,
  PageHeader,
  Panel,
  Spinner,
  StatusBadge,
  StoreBadge,
  Toggle,
  type MenuAction,
} from '@/components/ui'

export function AccountDetail() {
  const { id } = useParams()
  const accountId = Number(id)
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { push } = useToast()

  const [liveOpen, setLiveOpen] = useState(false)
  // The container's screen, for a sign-in window opened there. See
  // `components/ScreenView.tsx` for why it is a different thing from the live view.
  const [screenOpen, setScreenOpen] = useState(false)
  // The screen dialog is open to *finish a checkout* (un-driven window on the
  // purchase page), not to sign in. Only the copy differs; the mechanics are
  // the same window and the same close-and-check.
  const [finishing, setFinishing] = useState(false)
  // A live line of what the assisted sign-in is doing, shown over the screen.
  const [signInStep, setSignInStep] = useState<string | null>(null)
  // Watching a run on the container's screen.
  const [watchOpen, setWatchOpen] = useState(false)
  // Observing = jumped into a run someone else (the scheduler, Run now) started,
  // rather than owning a Run-and-watch. Closing an observed run must not stop it.
  const [watchObserving, setWatchObserving] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [confirmReset, setConfirmReset] = useState(false)

  const account = useQuery({
    queryKey: ['account', accountId],
    queryFn: () => api.accounts.get(accountId),
    // While a run or a live session is on, the state on this page changes
    // without the user doing anything, so it is polled. Ten seconds is a
    // compromise: fast enough that "Running" does not linger after a run ends,
    // slow enough that the page is not a request generator left open all day.
    refetchInterval: 10_000,
  })
  const runs = useQuery({
    queryKey: ['runs', accountId],
    queryFn: () => api.runs.list({ account_id: accountId, limit: 10 }),
    refetchInterval: 10_000,
  })
  const claims = useQuery({
    queryKey: ['claims', accountId],
    queryFn: () => api.claims.list({ account_id: accountId, limit: 25 }),
  })

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['account', accountId] })
    void queryClient.invalidateQueries({ queryKey: ['accounts'] })
    void queryClient.invalidateQueries({ queryKey: ['summary'] })
  }

  const runNow = useMutation({
    mutationFn: () => api.accounts.run(accountId),
    onSuccess: () => {
      push('The run has started. It takes a minute or two.', 'neutral')
      refresh()
      void queryClient.invalidateQueries({ queryKey: ['runs', accountId] })
    },
    onError: (error: Error) => push(error.message, 'critical'),
  })

  // A run you watch. It runs headed on the container's screen and holds the
  // browser open there when it finishes or fails, so the page it stopped on -
  // the checkout, most usefully - stays up to be looked at. Pressing Done
  // (stopWatch) releases it.
  const runWatch = useMutation({
    mutationFn: () => api.accounts.run(accountId, true),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['runs', accountId] })
    },
    onError: (error: Error) => {
      setWatchOpen(false)
      push(error.message, 'critical')
    },
  })
  const stopWatch = useMutation({
    mutationFn: () => api.accounts.stopWatching(accountId),
    onError: (error: Error) => push(error.message, 'critical'),
  })
  const startWatchedRun = () => {
    setWatchObserving(false)
    setWatchOpen(true)
    runWatch.mutate()
  }
  // Jump into the run already in progress - to watch it, or to solve a captcha
  // it is paused on - without starting another.
  const watchCurrentRun = () => {
    setWatchObserving(true)
    setWatchOpen(true)
  }
  const closeWatch = () => {
    // Only release the run if this window owns it. An observed run keeps going
    // (and keeps waiting for its captcha) after you stop looking.
    if (!watchObserving) stopWatch.mutate()
    setWatchOpen(false)
    setWatchObserving(false)
    refresh()
  }

  const clearAttention = useMutation({
    mutationFn: () => api.accounts.clearAttention(accountId),
    onSuccess: () => {
      push('Marked as sorted. The next run will be the real test.', 'good')
      refresh()
    },
    onError: (error: Error) => push(error.message, 'critical'),
  })

  const update = useMutation({
    mutationFn: (body: Parameters<typeof api.accounts.update>[1]) =>
      api.accounts.update(accountId, body),
    onSuccess: refresh,
    onError: (error: Error) => push(error.message, 'critical'),
  })

  // Can this machine put a browser window on a screen? Answered by the server,
  // because only it knows whether it is in a container.
  const localSignIn = useQuery({
    queryKey: ['can-sign-in-here', accountId],
    queryFn: () => api.accounts.canSignInHere(accountId),
    staleTime: 5 * 60_000,
  })

  const signInHere = useMutation({
    mutationFn: () => api.accounts.signInHere(accountId),
    onSuccess: () => {
      if (localSignIn.data?.via === 'screen') {
        // The window opened on Trove's own screen, so show it.
        setFinishing(false)
        setScreenOpen(true)
      } else {
        push('A browser window is opening. Sign in, then close it.', 'good')
      }
      refresh()
    },
    onError: (error: Error) => push(error.message, 'critical'),
  })

  // Finishing a claim the driven browser could not, because of a captcha it
  // cannot pass. It opens the *same* un-driven window as sign-in (holder "a
  // sign-in window"), only pointed at the checkout page, so the screen dialog,
  // the close button and the after-close check are all shared - the only
  // difference is what the person does in the window and what the copy says.
  const finishClaim = useMutation({
    mutationFn: () => api.accounts.finishClaim(accountId),
    onSuccess: () => {
      if (localSignIn.data?.via === 'screen') {
        setFinishing(true)
        setSignInStep(
          'Press “Add to library”, answer the captcha, accept. When the screen goes black the order is through — Trove closes up and checks your library on its own.',
        )
        setScreenOpen(true)
      } else {
        push(
          'A browser window is opening on the checkout page. Add the game to your library, then close it.',
          'good',
        )
      }
      refresh()
    },
    onError: (error: Error) => push(error.message, 'critical'),
  })

  // Can Trove type into the screen for them? Only asked once the screen is
  // the route in; on a desktop the person has their own password manager.
  const screenInfo = useQuery({
    queryKey: ['screen-available'],
    queryFn: api.screen.available,
    enabled: localSignIn.data?.via === 'screen',
    staleTime: 5 * 60_000,
  })

  const typeInto = useMutation({
    mutationFn: (what: 'email' | 'password' | 'code' | 'enter' | 'tab') =>
      api.accounts.typeIntoScreen(accountId, what),
    onError: (error: Error) => push(error.message, 'critical'),
  })

  // The assisted sign-in. It types the stored email and password into the
  // login form on the container's screen and presses Enter between them, the
  // way a person would, leaving only the captcha (and a 2FA code, if the
  // account uses one) to the human. It is best-effort by nature: the window
  // has no automation attached - that is the whole point - so Trove cannot see
  // the page and instead waits a beat between steps and trusts Epic's form to
  // be focused where a person would expect. If a step lands wrong, the single
  // Email / Password / Code buttons do the same thing one at a time.
  const [assisting, setAssisting] = useState(false)
  const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))
  // The steps come from the adapter, because the order is a fact about the
  // store's form and not about this page: Epic asks for the email, takes a
  // Continue and only then shows a password field, while Steam has both on one
  // page where that same Enter would submit an empty password - a failed login
  // attempt on the one account this app must not get locked. An empty list
  // means nobody has looked at that store's form, and then the button is not
  // rendered at all.
  //
  // A store can have more than one shape: GOG shows a remembered account one
  // password field, and typing the first-time steps into that puts the email
  // address in the password box. Trove cannot tell them apart - the window has
  // nothing attached to it, which is the whole reason it works - so each shape
  // is a button and the person presses the one on the screen.
  const signInForms = localSignIn.data?.sign_in_forms ?? []
  const assistedSignIn = async (form: SignInForm) => {
    setAssisting(true)
    try {
      for (const step of form.steps) {
        setSignInStep(step.label)
        await api.accounts.typeIntoScreen(accountId, step.action)
        await sleep(step.wait_ms)
      }
      setSignInStep(
        'If the store asks you to verify — a captcha, or a two-factor code — do that on the screen now. For a code, press Code. Then press Done.',
      )
    } catch (error) {
      setSignInStep(null)
      push(error instanceof Error ? error.message : 'The assisted sign-in stopped.', 'critical')
    } finally {
      setAssisting(false)
    }
  }

  const closeSignIn = useMutation({
    mutationFn: () => api.accounts.closeSignIn(accountId),
    onSuccess: () => {
      setScreenOpen(false)
      push(
        finishing
          ? 'The window is closing. Trove will check whether the game landed in your library.'
          : 'The window is closing. Trove will check the session in a moment.',
        'good',
      )
      setFinishing(false)
      refresh()
    },
    onError: (error: Error) => push(error.message, 'critical'),
  })

  const checkSessionNow = useMutation({
    mutationFn: () => api.accounts.checkSession(accountId),
    onSuccess: (account) => {
      push(
        account.status === 'ok'
          ? 'Signed in. Trove can take it from here.'
          : account.status_reason ?? 'Still signed out.',
        account.status === 'ok' ? 'good' : 'neutral',
      )
      refresh()
    },
    onError: (error: Error) => push(error.message, 'critical'),
  })

  const resetProfile = useMutation({
    mutationFn: () => api.accounts.resetProfile(accountId),
    onSuccess: () => {
      setConfirmReset(false)
      push('Fresh browser profile. Sign in again in the live view.', 'good')
      refresh()
    },
    onError: (error: Error) => {
      setConfirmReset(false)
      push(error.message, 'critical')
    },
  })

  const remove = useMutation({
    mutationFn: () => api.accounts.remove(accountId),
    onSuccess: () => {
      push('The account and its signed-in session are gone.', 'neutral')
      void queryClient.invalidateQueries({ queryKey: ['accounts'] })
      navigate('/accounts')
    },
    onError: (error: Error) => {
      setConfirmDelete(false)
      push(error.message, 'critical')
    },
  })

  if (account.isError) {
    return <ErrorState error={account.error} onRetry={() => void account.refetch()} />
  }
  if (!account.data) {
    return <div className="card skeleton h-40" />
  }

  const data = account.data
  const busy = Boolean(data.busy_with)
  const needsSignIn = data.status === 'never_signed_in'
  const needsHand = data.status === 'needs_attention'

  // What is happening right now, in a sentence, so a greyed-out button is
  // never the only signal. The immediate mutation states come first because
  // they are true the instant a button is pressed; `busy_with` is polled and
  // lags a little, and covers work started elsewhere (the scheduler, another
  // tab).
  const activity =
    signInHere.isPending
      ? 'Opening a sign-in window…'
      : finishClaim.isPending
        ? 'Opening the checkout…'
        : checkSessionNow.isPending
          ? 'Asking the store whether this account is signed in…'
          : runNow.isPending
            ? 'Starting a run…'
            : resetProfile.isPending
              ? 'Starting a fresh browser profile…'
              : data.busy_with === 'a checkout check'
                ? 'Checking whether the game landed in your library…'
                : data.busy_with === 'a sign-in check'
                  ? 'Checking whether this account is signed in…'
              : data.busy_with === 'a claim run'
                ? 'Checking the store for free games…'
                : data.busy_with === 'a sign-in window'
                  ? finishing
                    ? 'The checkout is open on Trove’s screen.'
                    : 'A sign-in window is open on Trove’s screen.'
                  : data.busy_with === 'the live view'
                    ? 'The live view is open.'
                    : null

  const viaScreen = localSignIn.data?.via === 'screen'
  const canUseScreen = viaScreen && Boolean(screenInfo.data?.ok)
  const signInWindowOpen = data.busy_with === 'a sign-in window'
  const runInProgress = data.busy_with === 'a claim run'
  const heldReason = busy ? `The browser profile is in use by ${data.busy_with}.` : undefined
  const canRun = !busy && !needsSignIn && !runNow.isPending
  // An account left `ok` with a reason on it has had something happen that is
  // not a fault of its own: a store challenged Trove's browser, or a checkout
  // is waiting in the un-driven window. The checkout has its own button and
  // its own words, so this is the other case.
  const challenged = data.status === 'ok' && Boolean(data.status_reason) && !data.checkout_pending

  /*
   * One primary action, chosen by what the account is actually doing.
   *
   * The header used to show up to seven buttons and the notice under it
   * repeated three of them, so the page asked a question - which of these am I
   * supposed to press? - instead of answering one. The order below is the
   * answer, and it is an order of urgency: something waiting on a person
   * first, then something in progress worth watching, then the ordinary run.
   * Nothing was dropped; the rest moved into the menu, with a line each saying
   * what it does, which a crowded strip had no room for.
   */
  const primary: {
    key: string
    label: string
    icon: React.ReactNode
    onSelect: () => void
    disabled?: boolean
    busy?: boolean
    title: string
  } =
    data.waiting_for_captcha && canUseScreen
      ? {
          key: 'captcha',
          label: 'Solve the captcha',
          icon: <Eye className="size-icon" />,
          onSelect: watchCurrentRun,
          title:
            'The run is paused on a captcha. Answer it on Trove’s screen and the claim carries on.',
        }
      : signInWindowOpen && viaScreen
        ? {
            key: 'screen',
            label: 'Show the screen',
            icon: <Monitor className="size-icon" />,
            onSelect: () => setScreenOpen(true),
            title: 'A browser window is already open on Trove’s screen. This shows it.',
          }
        : data.checkout_pending && localSignIn.data?.ok
          ? {
              key: 'finish',
              label: 'Finish the claim here',
              icon: <ExternalLink className="size-icon" />,
              onSelect: () => finishClaim.mutate(),
              disabled: busy || finishClaim.isPending,
              busy: finishClaim.isPending,
              title:
                heldReason ??
                (viaScreen
                  ? 'Open the checkout in a normal browser window on Trove’s screen and finish it there.'
                  : 'Open the checkout in a normal browser window on this machine and finish it there.'),
            }
          : runInProgress && canUseScreen
            ? {
                key: 'watch',
                label: 'Watch the run',
                icon: <Eye className="size-icon" />,
                onSelect: watchCurrentRun,
                title:
                  'Jump into the run in progress and watch it on Trove’s screen. Closing it does not stop the run.',
              }
            : (needsSignIn || needsHand) && localSignIn.data?.ok
              ? {
                  key: 'signin',
                  label: 'Sign in',
                  icon: <ExternalLink className="size-icon" />,
                  onSelect: () => signInHere.mutate(),
                  disabled: busy || signInHere.isPending,
                  busy: signInHere.isPending,
                  title:
                    heldReason ??
                    (viaScreen
                      ? 'Open this account in a normal browser window on Trove’s own screen, and show that screen here.'
                      : 'Open this account in a normal browser window on this machine.'),
                }
              : needsSignIn || needsHand
                ? {
                    key: 'live',
                    label: 'Sign in',
                    icon: <Monitor className="size-icon" />,
                    onSelect: () => setLiveOpen(true),
                    disabled: busy,
                    title: heldReason ?? 'Open this account in the live view and sign in there.',
                  }
                : {
                    key: 'run',
                    label: 'Run now',
                    icon: <Play className="size-icon" />,
                    onSelect: () => runNow.mutate(),
                    disabled: !canRun,
                    busy: runNow.isPending,
                    title: needsSignIn
                      ? 'Sign in to this account first. Trove has no password to sign in with.'
                      : (heldReason ?? 'Check this account for free games now.'),
                  }

  // Everything else, each with the sentence the strip had no room for.
  const menu: MenuAction[] = []
  if (primary.key !== 'run') {
    menu.push({
      key: 'run',
      label: 'Run now',
      description: 'Check the store for free games.',
      icon: <Play className="size-icon" />,
      onSelect: () => runNow.mutate(),
      disabled: !canRun,
      title: needsSignIn ? 'Sign in to this account first.' : (heldReason ?? ''),
    })
  }
  if (canUseScreen && !runInProgress) {
    menu.push({
      key: 'run-watch',
      label: 'Run and watch',
      description: 'Run it on the screen and hold the browser open at the end.',
      icon: <Eye className="size-icon" />,
      onSelect: startWatchedRun,
      disabled: !canRun,
      title: needsSignIn ? 'Sign in to this account first.' : (heldReason ?? ''),
    })
  }
  if (localSignIn.data?.ok && primary.key !== 'signin') {
    menu.push({
      key: 'signin',
      label: signInWindowOpen ? 'Show the sign-in window' : 'Sign in here',
      description: viaScreen
        ? 'A plain browser window on Trove’s screen, with nothing attached to it.'
        : 'A plain browser window on this machine, with nothing attached to it.',
      icon: <ExternalLink className="size-icon" />,
      onSelect: () => (signInWindowOpen && viaScreen ? setScreenOpen(true) : signInHere.mutate()),
      disabled: (busy && !signInWindowOpen) || signInHere.isPending,
      title: heldReason ?? '',
    })
  }
  if (primary.key !== 'live') {
    menu.push({
      key: 'live',
      label: 'Live view',
      description: 'The page as the driven browser sees it. A challenge can refuse it.',
      icon: <Monitor className="size-icon" />,
      onSelect: () => setLiveOpen(true),
      disabled: busy,
      title: heldReason ?? '',
    })
  }
  menu.push({
    key: 'check',
    label: 'Check the session',
    description: 'Ask the store whether this account is still signed in.',
    icon: <RefreshCw className="size-icon" />,
    onSelect: () => checkSessionNow.mutate(),
    disabled: busy || checkSessionNow.isPending,
    title: heldReason ?? '',
  })
  if (needsHand) {
    menu.push({
      key: 'sorted',
      label: 'I have sorted it',
      description: 'Clear the flag. The next run is the real test.',
      icon: <Check className="size-icon" />,
      onSelect: () => clearAttention.mutate(),
      disabled: clearAttention.isPending,
    })
  }

  return (
    <>
      {activity && (
        <div className="mb-4 flex items-center gap-2 rounded-tool border border-line-soft bg-dock px-4 py-2.5 text-body text-fg">
          <Spinner />
          <span className="min-w-0 flex-1">{activity}</span>
        </div>
      )}
      <PageHeader
        title={data.label}
        subtitle={
          <span className="flex flex-wrap items-center gap-2">
            {/* Which store this login belongs to, before the words. */}
            <span className="text-dim">
              <StoreBadge store={data.store} withName />
            </span>
            <StatusBadge
              tone={ACCOUNT_STATUS_TONE[data.status]}
              label={ACCOUNT_STATUS_LABEL[data.status]}
            />
            <span className="text-dim">
              {data.enabled ? everyHours(data.effective_interval_hours) : 'Paused'}
            </span>
          </span>
        }
        actions={
          <>
            <button
              type="button"
              className="btn-primary"
              onClick={primary.onSelect}
              disabled={primary.disabled}
              title={primary.title}
            >
              {primary.busy ? <Spinner /> : primary.icon}
              {primary.label}
            </button>
            <ActionMenu items={menu} />
          </>
        }
      />

      {/*
       * Where Trove has no screen of its own - a container, most often - the
       * live view is all there is, and it may not be enough: a store that puts
       * up an interactive captcha can refuse a browser it can tell is driven.
       * Saying so here is better than letting somebody discover it during a
       * sign-in that will not complete.
       */}
      {(needsSignIn || needsHand) && localSignIn.data && !localSignIn.data.ok && (
        <div className="notice mb-4">
          <p>
            {localSignIn.data.reason} A captcha may refuse the live view. The
            reliable way is to sign in on a desktop and copy that account&rsquo;s
            folder from <span className="keycap">data/profiles/</span> into this
            machine&rsquo;s, then choose Check the session under More.
          </p>
        </div>
      )}

      {/*
       * What is wrong and what the button above it does about it - and no
       * buttons of its own. It used to carry three, two of which were the same
       * actions as the header's, so the same page offered "Sign in here" and
       * "Open a browser window" as if they were different things. The notice
       * explains; the header acts.
       */}
      {(needsSignIn || needsHand) && (
        <div className="notice mb-4">
          <p>
            {data.status_reason ??
              (needsSignIn
                ? 'This account has not signed in yet.'
                : 'The last run stopped and needs a person.')}{' '}
            {localSignIn.data?.ok
              ? localSignIn.data.via === 'screen'
                ? 'Press Sign in above and a browser window opens on Trove’s screen; sign in by hand, close it, and Trove takes the session from there.'
                : 'Press Sign in above and a browser window opens on this machine; sign in by hand, close it, and Trove takes the session from there.'
              : 'Press Sign in above to open the live view and sign in there.'}
          </p>
        </div>
      )}

      {/*
       * A store challenged Trove's own browser. This is deliberately not the
       * attention notice: the account is not parked, nothing is waiting on a
       * person, and the schedule carries on - Cloudflare judges the browser and
       * the address on the visit in front of it, so the next run may well sail
       * through. Saying "needs a hand" here is what sent somebody round the
       * loop of signing in again to fix a session that was never broken.
       */}
      {challenged && (
        <div className="notice mb-4">
          <p>{data.status_reason}</p>
        </div>
      )}

      {data.status_screenshot && (
        <Panel title="What Trove saw" className="mb-4">
          <p className="mb-2 text-small text-dim">
            The page as it was when the run stopped, {relativeTime(data.status_at)}.
          </p>
          <a
            href={api.screenshotUrl(data.status_screenshot)}
            target="_blank"
            rel="noreferrer noopener"
            className="art block w-full"
            title="Open the full screenshot."
          >
            <img
              src={api.screenshotUrl(data.status_screenshot)}
              alt="The store page as it was when the run stopped."
              className="w-full"
            />
          </a>
        </Panel>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        {/*
         * The store's sign-in details, for the screen view only. Stored
         * encrypted, typed into the sign-in window when the person presses a
         * button there, and never used by a run: a scheduled login with a
         * stored password is exactly what a store's bot detection looks for,
         * and the whole design is sessions rather than logins. This is the
         * password-manager half of signing in through a remote picture, so
         * that answering a captcha is the only thing that needs a person.
         */}
        <Panel title="Store sign-in details">
          <div className="flex flex-col gap-3">
            <p className="text-small text-dim">
              Optional. When you sign in on Trove&rsquo;s screen, these are typed
              into the form for you at the press of a button, the way a password
              manager would. They are encrypted at rest and never used to sign in
              on a schedule; a claim run still needs the session you made by hand.
            </p>
            <Field label="Email" hint={data.login_email ? `Stored: ${data.login_email}` : 'Not stored.'}>
              <input
                className="field"
                type="email"
                autoComplete="off"
                defaultValue={data.login_email ?? ''}
                placeholder="you@example.com"
                onBlur={(event) => {
                  const value = event.target.value.trim()
                  if (value !== (data.login_email ?? '')) update.mutate({ login_email: value })
                }}
              />
            </Field>
            <Field
              label="Password"
              hint={data.has_login_password ? 'Stored. Type a new one to replace it, or clear the field to forget it.' : 'Not stored.'}
            >
              <input
                className="field"
                type="password"
                autoComplete="new-password"
                placeholder={data.has_login_password ? '••••••••' : ''}
                onBlur={(event) => {
                  const value = event.target.value
                  if (value) {
                    update.mutate({ login_password: value })
                    event.target.value = ''
                  }
                }}
              />
            </Field>
            {data.has_login_password && (
              <button
                type="button"
                className="btn-ghost self-start"
                onClick={() => update.mutate({ login_password: '' })}
              >
                Forget the password
              </button>
            )}
            <Field
              label="Authenticator secret"
              hint={
                data.has_totp
                  ? 'Stored. Trove can type the current code for you.'
                  : 'The "manual entry key" the store shows when you add an authenticator app. Optional.'
              }
            >
              <input
                className="field"
                type="password"
                autoComplete="off"
                placeholder={data.has_totp ? '••••••••' : 'ABCD EFGH IJKL MNOP'}
                onBlur={(event) => {
                  const value = event.target.value.trim()
                  if (value) {
                    update.mutate({ totp_secret: value })
                    event.target.value = ''
                  }
                }}
              />
            </Field>
            {data.has_totp && (
              <button
                type="button"
                className="btn-ghost self-start"
                onClick={() => update.mutate({ totp_secret: '' })}
              >
                Forget the authenticator secret
              </button>
            )}
          </div>
        </Panel>

        <Panel title="Settings">
          <div className="flex flex-col">
            <Toggle
              label="Check this account"
              description="When off, the schedule skips it. You can still run it by hand."
              checked={data.enabled}
              onChange={(enabled) => update.mutate({ enabled })}
            />
            <div className="border-t border-line-soft pt-3">
              <Field
                label="How often"
                hint={`Hours between runs. Leave empty to use the default, which is ${data.effective_interval_hours}.`}
              >
                <input
                  type="number"
                  min={1}
                  className="field"
                  defaultValue={data.interval_hours ?? ''}
                  placeholder={String(data.effective_interval_hours)}
                  onBlur={(event) => {
                    const raw = event.target.value.trim()
                    const value = raw === '' ? null : Number(raw)
                    if (value !== data.interval_hours) update.mutate({ interval_hours: value })
                  }}
                />
              </Field>
            </div>
            {/* Two sentences rather than one with a hole in it: with no
                scheduled run, `relativeTime` returns the en dash it uses for
                "no value", and "Next run – is not scheduled." reads as a
                missing word rather than as an answer. */}
            <p className="mt-3 text-small text-dim">
              {data.next_run_at
                ? `Next run ${relativeTime(data.next_run_at)}.`
                : 'No run is scheduled yet.'}{' '}
              Trove spreads runs a little either side of the interval so they do
              not land on the same minute every day.
            </p>
          </div>
        </Panel>

        <Panel title="Runs" count={runs.data?.length} bodyClassName="p-0">
          {(runs.data?.length ?? 0) === 0 ? (
            <EmptyState
              title="No runs yet"
              description="Every visit Trove makes to the store is listed here, with what it found."
            />
          ) : (
            <ul className="divide-y divide-line-soft">
              {(runs.data ?? []).map((run) => (
                <li key={run.id} className="px-strip py-2">
                  <div className="flex items-center gap-2">
                    <StatusBadge
                      tone={RUN_STATUS_TONE[run.status]}
                      label={RUN_STATUS_LABEL[run.status]}
                    />
                    <span className="text-small text-dim">
                      {run.trigger === 'manual' ? 'By hand' : 'Scheduled'}
                    </span>
                    <span
                      className="figure ml-auto text-tiny text-dim"
                      title={fullTime(run.started_at)}
                    >
                      {relativeTime(run.started_at)}
                    </span>
                  </div>
                  <p className="mt-1 text-small text-dim">
                    {run.message ?? (
                      <>
                        <span className="figure">{run.offers_seen}</span> free,{' '}
                        <span className="figure">{run.claimed}</span> claimed,{' '}
                        <span className="figure">{run.already_owned}</span> already owned, in{' '}
                        {duration(run.duration_s)}.
                      </>
                    )}
                  </p>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>

      <Panel title="Ledger" className="mt-4" count={claims.data?.length} bodyClassName="p-0">
        {(claims.data?.length ?? 0) === 0 ? (
          <EmptyState
            title="Nothing attempted yet"
            description="Every attempt on this account lands here, whatever came of it."
          />
        ) : (
          <ul className="divide-y divide-line-soft">
            {(claims.data ?? []).map((claim) => (
              <li key={claim.id} className="flex items-center gap-3 px-strip py-2">
                {/* The poster, same treatment as the dashboard's offer rows:
                    the block is drawn whether or not a picture arrives, so a
                    row claimed before Trove copied posters keeps the height
                    of its neighbours rather than jogging the list. */}
                <Art
                  src={claim.image_url}
                  store={claim.store}
                  className="aspect-wide w-art-row shrink-0"
                />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-control text-fg">{claim.title}</span>
                  {claim.detail && (
                    <span className="block truncate text-small text-dim">{claim.detail}</span>
                  )}
                </span>
                <StatusBadge
                  tone={OUTCOME_TONE[claim.outcome]}
                  label={OUTCOME_LABEL[claim.outcome]}
                />
                <span
                  className="figure hidden w-24 shrink-0 text-right text-tiny text-dim sm:block"
                  title={fullTime(claim.created_at)}
                >
                  {relativeTime(claim.created_at)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      {/*
       * Resetting the profile is not in the danger zone, and that is a
       * judgement rather than an oversight: it is the fix for a store that
       * keeps asking the same question, so somebody who needs it needs to find
       * it while they are annoyed, not after they have read to the bottom of
       * the page. It still confirms, because the session is real work to
       * replace.
       */}
      <Panel title="Browser profile" className="mt-4">
        <div className="flex flex-wrap items-center gap-3">
          <p className="min-w-0 flex-1 text-body text-dim">
            The signed-in session for this account lives in its own browser
            profile. If the store keeps showing the same challenge however many
            times you answer it, the profile itself has been flagged, and a
            fresh one is usually let straight through.
          </p>
          <button
            type="button"
            className="btn-outline"
            onClick={() => setConfirmReset(true)}
            disabled={busy}
            title={
              busy
                ? `The browser profile is in use by ${data.busy_with}.`
                : 'Throw away this profile and start a fresh one.'
            }
          >
            <RefreshCw className="size-icon" />
            Start a fresh profile
          </button>
        </div>
      </Panel>

      <div className="mt-6">
        <p className="eyebrow mb-2">Danger</p>
        <div className="card flex flex-wrap items-center gap-3 p-strip">
          <p className="min-w-0 flex-1 text-body text-dim">
            Deleting this account removes its ledger and its signed-in browser
            profile. You would have to sign in to the store by hand again.
          </p>
          <button type="button" className="btn-danger" onClick={() => setConfirmDelete(true)}>
            <Trash2 className="size-icon" />
            Delete this account
          </button>
        </div>
      </div>

      <ConfirmDialog
        open={confirmReset}
        onClose={() => setConfirmReset(false)}
        onConfirm={() => resetProfile.mutate()}
        busy={resetProfile.isPending}
        title="Start a fresh browser profile?"
        consequence="This account is signed out of the store and its cookies are deleted, so you have to sign in again by hand in the live view. Its ledger, its name and its schedule are kept, and nothing you have already claimed is affected."
        confirmLabel="Start a fresh profile"
      />

      <ConfirmDialog
        open={confirmDelete}
        onClose={() => setConfirmDelete(false)}
        onConfirm={() => remove.mutate()}
        busy={remove.isPending}
        title={`Delete ${data.label}?`}
        consequence="Its ledger rows and its signed-in browser profile are deleted. Trove cannot sign back in on its own, so you would have to do it by hand. Nothing you have already claimed on the store is affected."
        confirmLabel="Delete the account"
      />

      {/*
       * The live view is a large dialog rather than its own page, and that is
       * deliberate: it is a thing you do *to* this account and come back from,
       * and a route would let somebody bookmark a browser session.
       *
       * It refuses to close while the socket is up, through `busy`, so a
       * stray Escape in the middle of typing a password does not take the
       * window away mid-sign-in.
       */}
      <Dialog
        open={liveOpen}
        onClose={() => {
          setLiveOpen(false)
          refresh()
        }}
        title={`${data.label} in a browser`}
        subtitle="Sign in, or answer what the store is asking. Trove keeps the session."
        size="large"
        footerNote={
          <a
            href="https://store.epicgames.com/"
            target="_blank"
            rel="noreferrer noopener"
            className="inline-flex items-center gap-1.5 hover:text-fg"
          >
            <ExternalLink size={12} />
            The store, in your own browser
          </a>
        }
      >
        <div className="h-[74vh] min-h-[420px]">
          {liveOpen && (
            <LiveBrowser
              accountId={accountId}
              onClose={() => {
                setLiveOpen(false)
                refresh()
              }}
            />
          )}
        </div>
      </Dialog>

      {/*
       * The container's screen, with the un-driven sign-in window on it. It
       * can be closed and reopened freely - the window stays where it is -
       * which is why it does not refuse to close the way the live view does.
       * "Close the window" is the one thing it adds: there is no window
       * manager on that screen, so there may be nothing to click to close it.
       */}
      <Dialog
        open={screenOpen}
        onClose={() => {
          setScreenOpen(false)
          setFinishing(false)
          refresh()
        }}
        title={`${data.label} on Trove's screen`}
        subtitle={
          finishing
            ? 'The checkout, in a normal browser window with nothing attached to it — the one Epic’s captcha accepts. Add the game to your library and answer the captcha. When the order goes through Epic closes its own window and the screen goes black: that is the finish, and Trove takes it from there.'
            : // The store's own caveat rides here rather than in a tooltip: on
              // most stores an unticked "Remember me" is a session cookie no
              // profile can keep, so it has to be read before the sign-in and
              // not discovered after it.
              'A normal browser window, opened inside the container with nothing attached to it: no automation, no remote control, only you. Trove keeps the session and never sees your password.' +
              (localSignIn.data?.sign_in_note ? ' ' + localSignIn.data.sign_in_note : '')
        }
        size="large"
      >
        <div className="h-[74vh] min-h-[420px]">
          {screenOpen && (
            <ScreenView
              status={signInStep}
              onClose={() => {
                setScreenOpen(false)
                setSignInStep(null)
                setFinishing(false)
                refresh()
              }}
              /*
               * Two things, and they are different kinds of thing, so they
               * look different and sit apart: a cluster that puts text into
               * the window over there, and the one button that finishes.
               *
               * The bar used to be seven controls of four shapes in a line -
               * a filled button, three bare words, a lone arrow glyph, a
               * paragraph - with nothing saying that five of them were the
               * same job. They are now one bordered group under a label,
               * every item an icon and a word, in the order a sign-in form is
               * filled: the whole thing first, then the pieces.
               */
              footer={({ paste, ready }) => (
                <>
                  {(screenInfo.data?.typing || ready) && (
                    <span className="flex min-w-0 flex-wrap items-center gap-1 rounded-tool border border-line-soft bg-dock px-2 py-1.5">
                      <span className="px-1 text-tiny text-dim">Type into the window</span>
                      {/* One press that types the email, Enter, the password
                          and Enter, with the captcha left to the person.
                          Best-effort, because the window has nothing attached
                          for Trove to read; the single buttons beside it are
                          the reliable fallback. */}
                      {screenInfo.data?.typing &&
                        data.login_email &&
                        data.has_login_password &&
                        signInForms.map((form) => (
                          <button
                            key={form.key}
                            type="button"
                            className="btn-secondary"
                            disabled={assisting}
                            title={form.description}
                            onClick={() => void assistedSignIn(form)}
                          >
                            {assisting ? <Spinner /> : <LogIn className="size-icon" />}
                            {/* One shape, one button, and it can say the plain
                                thing. Several shapes and each says which form
                                it fills in, because that is the choice being
                                made. */}
                            {signInForms.length === 1 ? 'Sign in for me' : form.label}
                          </button>
                        ))}
                      {/* The whole sequence on the left of the line, the
                          single keys on the right of it. Without the rule they
                          read as six controls of equal weight, which is what
                          made the bar a guess. */}
                      {screenInfo.data?.typing &&
                        data.login_email &&
                        data.has_login_password &&
                        signInForms.length > 0 && (
                          <span aria-hidden="true" className="mx-1 h-4 w-px bg-line" />
                        )}
                      {/* The password-manager half: click a field on the
                          screen, press the button, Trove types it there. */}
                      {screenInfo.data?.typing && (
                        <>
                          <button
                            type="button"
                            className="btn-ghost"
                            disabled={!data.login_email || typeInto.isPending}
                            title={
                              data.login_email
                                ? `Type ${data.login_email} into the field you clicked.`
                                : 'No email stored; see Store sign-in details on the account page.'
                            }
                            onClick={() => typeInto.mutate('email')}
                          >
                            <AtSign className="size-icon" />
                            Email
                          </button>
                          <button
                            type="button"
                            className="btn-ghost"
                            disabled={!data.has_login_password || typeInto.isPending}
                            title={
                              data.has_login_password
                                ? 'Type the stored password into the field you clicked.'
                                : 'No password stored; see Store sign-in details on the account page.'
                            }
                            onClick={() => typeInto.mutate('password')}
                          >
                            <KeyRound className="size-icon" />
                            Password
                          </button>
                          <button
                            type="button"
                            className="btn-ghost"
                            disabled={!data.has_totp || typeInto.isPending}
                            title={
                              data.has_totp
                                ? 'Type the current authenticator code into the field you clicked.'
                                : 'No authenticator secret stored.'
                            }
                            onClick={() => typeInto.mutate('code')}
                          >
                            <Hash className="size-icon" />
                            Code
                          </button>
                          <button
                            type="button"
                            className="btn-ghost"
                            disabled={typeInto.isPending}
                            title="Press Enter on the screen, so your hands can stay this side of the picture."
                            onClick={() => typeInto.mutate('enter')}
                          >
                            <CornerDownLeft className="size-icon" />
                            Enter
                          </button>
                        </>
                      )}
                      {/* Your own clipboard, not a stored detail - but the same
                          job, so it belongs in the same group rather than
                          floating at the far end of the bar as it did. */}
                      <button
                        type="button"
                        className="btn-ghost"
                        onClick={paste}
                        disabled={!ready}
                        title="Send what is on your clipboard to the screen, then press Ctrl+V in the field over there."
                      >
                        <ClipboardPaste className="size-icon" />
                        Paste
                      </button>
                    </span>
                  )}
                  <span className="flex-1" />
                  <button
                    type="button"
                    className="btn-primary"
                    onClick={() => closeSignIn.mutate()}
                    disabled={closeSignIn.isPending || data.busy_with !== 'a sign-in window'}
                    title={
                      data.busy_with !== 'a sign-in window'
                        ? 'There is no window open for this account.'
                        : finishing
                          ? 'Close the checkout window. Trove then checks whether the game landed in your library.'
                          : 'Close the sign-in window. Trove then checks whether the account is signed in.'
                    }
                  >
                    {closeSignIn.isPending ? <Spinner /> : <Check className="size-icon" />}
                    {/* Neutral on purpose. This button closes the window and
                        asks the store what happened; it must not put words in
                        somebody's mouth about a sign-in that may not have
                        worked. The tooltip says what follows. */}
                    Done, close the window
                  </button>
                </>
              )}
            />
          )}
        </div>
      </Dialog>

      {/*
       * Watching a run. The claim runs headed on the container's display, which
       * this streams over VNC, and a watched run holds the browser open on
       * whatever page it ends on - so a checkout that Epic has changed is right
       * there to read rather than gone before it can be seen. Done releases the
       * hold; closing the dialog does the same, so the run is never left
       * holding a browser nobody is watching.
       */}
      <Dialog
        open={watchOpen}
        onClose={closeWatch}
        title={`Watching ${data.label}`}
        subtitle={
          watchObserving
            ? 'The run in progress, live on Trove’s screen. If it is waiting on a captcha, solve it here and it carries on.'
            : 'The run, live on Trove’s screen. It opens the store, checks you are signed in, then tries the checkout.'
        }
        size="large"
      >
        <div className="h-[74vh] min-h-[420px]">
          {watchOpen && (
            <ScreenView
              status={
                data.waiting_for_captcha
                  ? '⚠ Epic wants a captcha — solve it on the screen below. The claim continues on its own the moment it clears.'
                  : data.status === 'needs_attention'
                    ? (data.status_reason ?? 'The run stopped and needs a hand.')
                    : data.busy_with === 'a claim run'
                      ? watchObserving
                        ? 'Watching the run in progress.'
                        : 'Running… the browser is held open here until you press Done.'
                      : 'Starting the run…'
              }
              onClose={closeWatch}
              footer={
                <>
                  <span className="flex-1" />
                  <button type="button" className="btn-primary" onClick={closeWatch}>
                    <Check className="size-icon" />
                    {watchObserving ? 'Close, leave the run going' : 'Done, close the browser'}
                  </button>
                </>
              }
            />
          )}
        </div>
      </Dialog>
    </>
  )
}
