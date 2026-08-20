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
import { CornerDownLeft, ExternalLink, Eye, KeyRound, Monitor, Play, RefreshCw, Trash2 } from 'lucide-react'
import { api } from '@/lib/api'
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
  ConfirmDialog,
  Dialog,
  EmptyState,
  ErrorState,
  Field,
  PageHeader,
  Panel,
  Spinner,
  StatusBadge,
  Toggle,
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
  // A live line of what the assisted sign-in is doing, shown over the screen.
  const [signInStep, setSignInStep] = useState<string | null>(null)
  // Watching a run on the container's screen.
  const [watchOpen, setWatchOpen] = useState(false)
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
    setWatchOpen(true)
    runWatch.mutate()
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
        setScreenOpen(true)
      } else {
        push('A browser window is opening. Sign in, then close it.', 'good')
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
  const assistedSignIn = async () => {
    setAssisting(true)
    try {
      setSignInStep('Typing your email…')
      await api.accounts.typeIntoScreen(accountId, 'email')
      await sleep(400)
      setSignInStep('Continuing…')
      await api.accounts.typeIntoScreen(accountId, 'enter')
      setSignInStep('Waiting for the password step to load…')
      await sleep(3000)
      setSignInStep('Typing your password…')
      await api.accounts.typeIntoScreen(accountId, 'password')
      await sleep(400)
      setSignInStep('Signing in…')
      await api.accounts.typeIntoScreen(accountId, 'enter')
      await sleep(2500)
      setSignInStep(
        'If Epic asks you to verify — a captcha, or a two-factor code — do that on the screen now. For a code, press Code. Then press Done.',
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
      push('The window is closing. Trove will check the session in a moment.', 'good')
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
      : checkSessionNow.isPending
        ? 'Asking the store whether this account is signed in…'
        : runNow.isPending
          ? 'Starting a run…'
          : resetProfile.isPending
            ? 'Starting a fresh browser profile…'
            : data.busy_with === 'a sign-in check'
              ? 'Checking whether this account is signed in…'
              : data.busy_with === 'a claim run'
                ? 'Checking the store for free games…'
                : data.busy_with === 'a sign-in window'
                  ? 'A sign-in window is open on Trove’s screen.'
                  : data.busy_with === 'the live view'
                    ? 'The live view is open.'
                    : null

  return (
    <>
      {activity && (
        <div className="mb-4 flex items-center gap-2 rounded-control border border-line-soft bg-raised px-4 py-2.5 text-body text-fg">
          <Spinner />
          {activity}
        </div>
      )}
      <PageHeader
        title={data.label}
        subtitle={
          <span className="flex flex-wrap items-center gap-2">
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
            {/*
             * The primary way in, where the machine can manage it. It opens an
             * ordinary browser window with no automation attached, which is the
             * only thing a store's challenge will reliably accept an answer
             * from. The live view stays for the container case and is offered
             * beside it rather than instead of it.
             */}
            {localSignIn.data?.ok && (
              <button
                type="button"
                className="btn-secondary"
                onClick={() => signInHere.mutate()}
                disabled={busy || signInHere.isPending}
                title={
                  busy
                    ? `The browser profile is in use by ${data.busy_with}.`
                    : localSignIn.data.via === 'screen'
                      ? "Open this account in a normal browser window on Trove's own screen, and show that screen here."
                      : 'Open this account in a normal browser window on this machine.'
                }
              >
                {signInHere.isPending ? <Spinner /> : <ExternalLink className="size-icon" />}
                Sign in here
              </button>
            )}
            {/* The window is already open on the container's screen: offer
                the screen itself rather than a second window. */}
            {localSignIn.data?.via === 'screen' && data.busy_with === 'a sign-in window' && (
              <button type="button" className="btn-secondary" onClick={() => setScreenOpen(true)}>
                <Monitor className="size-icon" />
                Show the screen
              </button>
            )}
            <button
              type="button"
              className="btn-secondary"
              onClick={() => setLiveOpen(true)}
              disabled={busy}
              // A control that lies is worse than none: when the profile is
              // held, the button says so rather than opening a window that
              // closes itself.
              title={
                busy
                  ? `The browser profile is in use by ${data.busy_with}.`
                  : 'Open this account in a real browser.'
              }
            >
              <Monitor className="size-icon" />
              Live view
            </button>
            {localSignIn.data?.via === 'screen' && screenInfo.data?.ok && (
              <button
                type="button"
                className="btn-secondary"
                onClick={startWatchedRun}
                disabled={runNow.isPending || busy || needsSignIn}
                title={
                  needsSignIn
                    ? 'Sign in to this account first.'
                    : busy
                      ? `The browser profile is in use by ${data.busy_with}.`
                      : 'Run now and watch it happen on Trove’s screen, held open on whatever page it stops at.'
                }
              >
                <Eye className="size-icon" />
                Run and watch
              </button>
            )}
            <button
              type="button"
              className="btn-primary"
              onClick={() => runNow.mutate()}
              disabled={runNow.isPending || busy || needsSignIn}
              title={
                needsSignIn
                  ? 'Sign in to this account first. Trove has no password to sign in with.'
                  : busy
                    ? `The browser profile is in use by ${data.busy_with}.`
                    : 'Check this account for free games now.'
              }
            >
              {runNow.isPending ? <Spinner /> : <Play className="size-icon" />}
              Run now
            </button>
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
            machine&rsquo;s, then press Check again.
          </p>
        </div>
      )}

      {(needsSignIn || needsHand) && (
        <div className="notice mb-4 flex flex-wrap items-center gap-3">
          <p className="min-w-0 flex-1">
            {data.status_reason ??
              (localSignIn.data?.ok
                ? localSignIn.data.via === 'screen'
                  ? "This account has not signed in yet. Open a browser window on Trove's screen, sign in by hand, then close it. Trove takes it from there."
                  : 'This account has not signed in yet. Open a browser window, sign in by hand, then close it. Trove takes it from there.'
                : 'This account has not signed in yet. Open the live view, sign in by hand, and Trove takes it from there.')}
          </p>
          {localSignIn.data?.ok ? (
            <button
              type="button"
              className="btn-secondary"
              onClick={() => signInHere.mutate()}
              disabled={busy || signInHere.isPending}
            >
              {signInHere.isPending ? <Spinner /> : null}
              Open a browser window
            </button>
          ) : (
            <button type="button" className="btn-secondary" onClick={() => setLiveOpen(true)}>
              Open the live view
            </button>
          )}
          {/* Normally the sign-in window closing checks this by itself. The
              button is for when nothing was watching: Trove restarted while the
              window was open, or the profile was signed in some other way. */}
          <button
            type="button"
            className="btn-ghost"
            onClick={() => checkSessionNow.mutate()}
            disabled={busy || checkSessionNow.isPending}
            title="Ask the store whether this account is signed in now."
          >
            {checkSessionNow.isPending ? <Spinner /> : null}
            Check again
          </button>
          {needsHand && (
            <button
              type="button"
              className="btn-ghost"
              onClick={() => clearAttention.mutate()}
              disabled={clearAttention.isPending}
            >
              I have sorted it
            </button>
          )}
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
        <div className="h-[60vh] min-h-[380px]">
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
          refresh()
        }}
        title={`${data.label} on Trove's screen`}
        subtitle="A normal browser window, opened inside the container with nothing attached to it."
        size="large"
      >
        <div className="h-[60vh] min-h-[380px]">
          {screenOpen && (
            <ScreenView
              status={signInStep}
              onClose={() => {
                setScreenOpen(false)
                setSignInStep(null)
                refresh()
              }}
              footer={
                <>
                  {/* One press that types the email, Enter, the password and
                      Enter, with the captcha left to the person. Best-effort,
                      because the window has nothing attached for Trove to read;
                      the single buttons beside it are the reliable fallback. */}
                  {screenInfo.data?.typing && data.login_email && data.has_login_password && (
                    <button
                      type="button"
                      className="btn-secondary"
                      disabled={assisting}
                      title="Type your email and password into the login form and press Enter between them. You still answer the captcha."
                      onClick={() => void assistedSignIn()}
                    >
                      {assisting ? <Spinner /> : <KeyRound className="size-icon" />}
                      Sign in for me
                    </button>
                  )}
                  {/* The password-manager half: click a field on the screen,
                      press the button, Trove types it there. Enter is the
                      one key worth a button, so the hands can stay on this
                      side of the picture. */}
                  {screenInfo.data?.typing && (
                    <span className="flex flex-wrap items-center gap-1">
                      <button
                        type="button"
                        className="btn-ghost"
                        disabled={!data.login_email || typeInto.isPending}
                        title={data.login_email ? `Type ${data.login_email} into the field you clicked.` : 'No email stored; see Store sign-in details below.'}
                        onClick={() => typeInto.mutate('email')}
                      >
                        Email
                      </button>
                      <button
                        type="button"
                        className="btn-ghost"
                        disabled={!data.has_login_password || typeInto.isPending}
                        title={data.has_login_password ? 'Type the stored password into the field you clicked.' : 'No password stored; see Store sign-in details below.'}
                        onClick={() => typeInto.mutate('password')}
                      >
                        <KeyRound className="size-icon" />
                        Password
                      </button>
                      <button
                        type="button"
                        className="btn-ghost"
                        disabled={!data.has_totp || typeInto.isPending}
                        title={data.has_totp ? 'Type the current authenticator code.' : 'No authenticator secret stored.'}
                        onClick={() => typeInto.mutate('code')}
                      >
                        Code
                      </button>
                      <button
                        type="button"
                        className="btn-ghost"
                        disabled={typeInto.isPending}
                        title="Press Enter on the screen."
                        onClick={() => typeInto.mutate('enter')}
                      >
                        <CornerDownLeft className="size-icon" />
                      </button>
                    </span>
                  )}
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => closeSignIn.mutate()}
                  disabled={closeSignIn.isPending || data.busy_with !== 'a sign-in window'}
                  title={
                    data.busy_with === 'a sign-in window'
                      ? 'Close the sign-in window. Trove then checks whether the account is signed in.'
                      : 'There is no sign-in window open for this account.'
                  }
                >
                  {closeSignIn.isPending ? <Spinner /> : null}
                  Done, close the window
                </button>
                </>
              }
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
        onClose={() => {
          stopWatch.mutate()
          setWatchOpen(false)
          refresh()
        }}
        title={`Watching ${data.label}`}
        subtitle="The run, live on Trove’s screen. It opens the store, checks you are signed in, then tries the checkout."
        size="large"
      >
        <div className="h-[60vh] min-h-[380px]">
          {watchOpen && (
            <ScreenView
              status={
                data.waiting_for_captcha
                  ? '⚠ Epic wants a captcha — solve it on the screen below. The claim continues on its own the moment it clears.'
                  : data.status === 'needs_attention'
                    ? (data.status_reason ?? 'The run stopped and needs a hand.')
                    : data.busy_with === 'a claim run'
                      ? 'Running… the browser is held open here until you press Done.'
                      : 'Starting the run…'
              }
              onClose={() => {
                stopWatch.mutate()
                setWatchOpen(false)
                refresh()
              }}
              footer={
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => {
                    stopWatch.mutate()
                    setWatchOpen(false)
                    refresh()
                  }}
                >
                  Done
                </button>
              }
            />
          )}
        </div>
      </Dialog>
    </>
  )
}
