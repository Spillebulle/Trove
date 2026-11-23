/*
 * Settings, in the shape STYLE-GUIDE 9 asks for.
 *
 * There is no Save button: every setting applies as it is changed, and the
 * footer of each panel says so. A setting that needs saving is a setting the
 * user can get wrong by walking away.
 *
 * The webhook field is the one exception, and it is not really one: it saves on
 * blur rather than per keystroke, because a URL half-typed is not a URL and
 * writing one per character would put a dozen broken webhooks through the
 * encryption path for every good one.
 */
import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { KeyRound, Plus, Send, Stethoscope } from 'lucide-react'
import { api, type BrowserDiagnostics } from '@/lib/api'
import { useApp, useToast, type ThemeChoice } from '@/lib/app-context'
import { REDACTED, type ApiKey, type ApiKeyCreated, type ApiKeyScope } from '@/lib/types'
import { fullTime, relativeTime } from '@/lib/utils'
import {
  ConfirmDialog,
  CopyButton,
  Dialog,
  ErrorState,
  Field,
  PageHeader,
  Panel,
  Segmented,
  Spinner,
  StatusBadge,
  Toggle,
} from '@/components/ui'

const CHANNELS = [
  { value: 'off', label: 'Off' },
  { value: 'discord', label: 'Discord' },
  { value: 'webhook', label: 'Webhook' },
] as const

const THEMES: ReadonlyArray<{ value: ThemeChoice; label: string }> = [
  { value: 'dark', label: 'Dark' },
  { value: 'light', label: 'Light' },
  { value: 'system', label: 'System' },
]

export function Settings() {
  const queryClient = useQueryClient()
  const { push } = useToast()
  const { theme, setTheme } = useApp()

  const settings = useQuery({ queryKey: ['settings'], queryFn: api.settings.read })

  const write = useMutation({
    mutationFn: (values: Record<string, unknown>) => api.settings.write(values),
    onSuccess: (data) => queryClient.setQueryData(['settings'], data),
    onError: (error: Error) => push(error.message, 'critical'),
  })

  const values = settings.data?.values ?? {}
  const channel = (values['notify.channel'] as string) ?? 'off'
  const hasWebhook = values['notify.webhook_url'] === REDACTED

  // The field holds what the user is typing. It starts empty even when a
  // webhook is stored, because the API never sends the stored value back; the
  // placeholder says it is set. Submitting an empty field leaves it alone.
  const [webhook, setWebhook] = useState('')
  useEffect(() => {
    setWebhook('')
  }, [channel])

  const test = useMutation({
    mutationFn: () => api.settings.testNotification(channel, webhook || undefined),
    onSuccess: (result) => push(result.message, result.ok ? 'good' : 'critical'),
    onError: (error: Error) => push(error.message, 'critical'),
  })

  const password = useMutation({
    mutationFn: ({ current, next }: { current: string; next: string }) =>
      api.auth.changePassword(current, next),
    onSuccess: () => push('Your password is changed.', 'good'),
    onError: (error: Error) => push(error.message, 'critical'),
  })

  if (settings.isError) {
    return <ErrorState error={settings.error} onRetry={() => void settings.refetch()} />
  }

  const set = (key: string, value: unknown) => write.mutate({ [key]: value })

  return (
    <>
      <PageHeader title="Settings" subtitle="Everything here applies as you change it." />

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="Schedule">
          <Toggle
            label="Check accounts on a schedule"
            description="When off, nothing runs on its own and you claim by pressing Run now."
            checked={Boolean(values['schedule.enabled'])}
            onChange={(value) => set('schedule.enabled', value)}
          />
          <Toggle
            label="Sign back in automatically"
            description="When an account is found signed out, Trove signs in with the email and password stored on it. A captcha or an emailed code still needs you."
            checked={Boolean(values['signin.automatic'])}
            onChange={(value) => set('signin.automatic', value)}
          />
          <p className="mt-2 border-t border-line-soft pt-3 text-small text-dim">
            Each account has its own interval, set on its own page. Runs are
            spread either side of it so they do not land on the same minute
            every day, and an account waiting for a hand is skipped rather than
            hammered.
          </p>
        </Panel>

        <Panel title="Appearance">
          <div className="flex items-center justify-between gap-4 py-2">
            <span className="min-w-0">
              <span className="block text-control text-fg">Theme</span>
              <span className="mt-0.5 block text-small text-dim">
                System follows whatever your computer is set to.
              </span>
            </span>
            <Segmented options={THEMES} value={theme} onChange={setTheme} label="Theme" />
          </div>
        </Panel>
      </div>

      <Panel title="Notifications" className="mt-4">
        <div className="flex items-center justify-between gap-4 py-2">
          <span className="min-w-0">
            <span className="block text-control text-fg">Where messages go</span>
            <span className="mt-0.5 block text-small text-dim">
              Discord posts a proper embed. Webhook posts plain JSON, for ntfy,
              Gotify or a script of your own.
            </span>
          </span>
          <Segmented
            options={CHANNELS}
            value={channel as (typeof CHANNELS)[number]['value']}
            onChange={(value) => set('notify.channel', value)}
            label="Notification channel"
          />
        </div>

        {channel !== 'off' && (
          <div className="mt-3 border-t border-line-soft pt-3">
            <Field
              label={channel === 'discord' ? 'Discord webhook URL' : 'Webhook URL'}
              hint={
                channel === 'discord'
                  ? 'In Discord: Server settings, Integrations, Webhooks, New webhook, Copy webhook URL.'
                  : 'Trove posts a flat JSON body: app, title, message, severity, context, url.'
              }
            >
              <input
                type="url"
                className="field"
                value={webhook}
                placeholder={
                  hasWebhook
                    ? 'A webhook is saved. Type a new one to replace it.'
                    : 'https://discord.com/api/webhooks/...'
                }
                onChange={(event) => setWebhook(event.target.value)}
                onBlur={() => {
                  if (webhook.trim()) set('notify.webhook_url', webhook.trim())
                }}
                autoComplete="off"
                spellCheck={false}
              />
            </Field>

            <p className="mt-2 text-small text-dim">
              The URL is encrypted in the database and is never sent back to
              this page. It is a way into your channel, so treat it like a
              password.
            </p>

            <div className="mt-3 flex flex-wrap items-center gap-2">
              <button
                type="button"
                className="btn-secondary"
                onClick={() => test.mutate()}
                disabled={test.isPending || (!webhook.trim() && !hasWebhook)}
                title={
                  !webhook.trim() && !hasWebhook
                    ? 'Enter a webhook URL first.'
                    : 'Send one test message now.'
                }
              >
                {test.isPending ? <Spinner /> : <Send className="size-icon" />}
                Send a test message
              </button>
            </div>

            <PublicUrlField
              stored={(values['notify.public_url'] as string) ?? ''}
              onSave={(value) => set('notify.public_url', value)}
            />

            <div className="mt-4 border-t border-line-soft pt-2">
              <p className="eyebrow mb-1">What is worth a message</p>
              <Toggle
                label="A game was claimed"
                checked={Boolean(values['notify.on_claimed'])}
                onChange={(value) => set('notify.on_claimed', value)}
              />
              <Toggle
                label="An account needs a hand"
                description="A captcha, a sign-in, or anything else Trove will not guess at."
                checked={Boolean(values['notify.on_attention'])}
                onChange={(value) => set('notify.on_attention', value)}
              />
              <Toggle
                label="A run failed"
                checked={Boolean(values['notify.on_failed'])}
                onChange={(value) => set('notify.on_failed', value)}
              />
              <Toggle
                label="A summary after every run that claimed something"
                description="Off by default. One message per account per run is how a channel gets muted."
                checked={Boolean(values['notify.on_run_summary'])}
                onChange={(value) => set('notify.on_run_summary', value)}
              />
            </div>
          </div>
        )}
      </Panel>

      <Panel title="Your Trove password" className="mt-4">
        <PasswordForm
          busy={password.isPending}
          onSubmit={(current, next) => password.mutate({ current, next })}
        />
      </Panel>

      <ApiKeys />

      <BrowserCheck />

      <p className="mt-6 text-small text-dim">
        Trove signs in to stores you already have accounts with, on your own
        machine. Automating a store login can breach that store&rsquo;s terms of
        service, and that is your call to make rather than the app&rsquo;s.
      </p>
    </>
  )
}

/*
 * API keys, for scripts and agents - the same shape as Tally's and HomeLab
 * Manager's, so a key works the same way across the family.
 *
 * Making one is a dialog rather than an inline form because of what comes
 * after: the plaintext exists only in the create response, and a dialog can
 * hold it on screen until the person deliberately closes it, where an inline
 * notice is one stray click from gone.
 */
const SCOPES: ReadonlyArray<{ value: ApiKeyScope; label: string }> = [
  { value: 'full', label: 'Full' },
  { value: 'read_only', label: 'Read-only' },
]

const SCOPE_LABEL: Record<ApiKeyScope, string> = {
  full: 'Full access',
  read_only: 'Read-only',
}

const SCOPE_HELP: Record<ApiKeyScope, string> = {
  full: 'Reads everything and can act: run an account, check a session, change settings. Give it only to something you would trust with your password.',
  read_only:
    'Reads accounts, runs, offers and the ledger, and changes nothing. Every write is refused.',
}

// Trove cannot see the address a person reaches it at, so a message can only
// link back to an account page once somebody says what that address is. The
// address this page is open on is almost always the right answer, so it is
// offered rather than typed.
function PublicUrlField({ stored, onSave }: { stored: string; onSave: (value: string) => void }) {
  const [value, setValue] = useState(stored)
  useEffect(() => setValue(stored), [stored])
  const here = window.location.origin
  const save = (next: string) => {
    const clean = next.trim().replace(/\/+$/, '')
    if (clean !== stored) onSave(clean)
  }
  return (
    <div className="mt-4 border-t border-line-soft pt-3">
      <Field
        label="Trove's address"
        hint="Messages about an account link to its page here, so “finish the claim” is one tap from the message. Leave empty for no links."
      >
        <div className="flex flex-wrap items-center gap-2">
          <input
            type="url"
            className="field min-w-0 flex-1"
            value={value}
            placeholder={here}
            onChange={(event) => setValue(event.target.value)}
            onBlur={() => save(value)}
            autoComplete="off"
            spellCheck={false}
          />
          {stored !== here && (
            <button type="button" className="btn-secondary" onClick={() => save(here)}>
              Use this address
            </button>
          )}
        </div>
      </Field>
    </div>
  )
}

function ApiKeys() {
  const queryClient = useQueryClient()
  const { push } = useToast()
  const [creating, setCreating] = useState(false)
  const [revoking, setRevoking] = useState<ApiKey | null>(null)

  const keys = useQuery({ queryKey: ['api-keys'], queryFn: api.apiKeys.list })

  const revoke = useMutation({
    mutationFn: (id: number) => api.apiKeys.revoke(id),
    onSuccess: () => {
      setRevoking(null)
      push('The key is revoked.', 'good')
      void queryClient.invalidateQueries({ queryKey: ['api-keys'] })
    },
    onError: (error: Error) => push(error.message, 'critical'),
  })

  const all = keys.data ?? []
  const live = all.filter((key) => !key.revoked_at)

  return (
    <Panel
      title="API keys"
      count={live.length || undefined}
      className="mt-4"
      commands={
        <button
          type="button"
          className="btn-secondary"
          onClick={() => setCreating(true)}
          title="Make a key for a script or another app."
        >
          <Plus className="size-icon" />
          New key
        </button>
      }
    >
      {keys.isError ? (
        <ErrorState error={keys.error} onRetry={() => void keys.refetch()} />
      ) : keys.isLoading ? (
        <p className="flex items-center gap-2 text-small text-dim">
          <Spinner /> Loading the keys.
        </p>
      ) : all.length === 0 ? (
        <p className="text-small text-dim">
          No keys yet. A key lets a script or an assistant read what Trove is doing, or
          press its buttons, without your password.
        </p>
      ) : (
        <ul className="divide-y divide-line-soft">
          {all.map((key) => (
            <li
              key={key.id}
              className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2 py-2"
            >
              <div className="min-w-0 flex-1">
                <p className="flex items-center gap-2">
                  <KeyRound className="size-icon shrink-0 text-dim" aria-hidden="true" />
                  <span
                    className={
                      'truncate text-control ' + (key.revoked_at ? 'text-dim' : 'text-fg')
                    }
                  >
                    {key.name}
                  </span>
                  <StatusBadge
                    tone="neutral"
                    label={key.revoked_at ? 'Revoked' : (SCOPE_LABEL[key.scope] ?? key.scope)}
                  />
                </p>
                <p className="mt-0.5 text-small text-dim">
                  <span className="figure">{key.prefix}&hellip;</span> &middot; made{' '}
                  <span title={fullTime(key.created_at)}>{relativeTime(key.created_at)}</span>{' '}
                  &middot;{' '}
                  {key.last_used_at ? (
                    <span title={fullTime(key.last_used_at)}>
                      last used {relativeTime(key.last_used_at)}
                    </span>
                  ) : (
                    'never used'
                  )}
                </p>
              </div>
              {!key.revoked_at && (
                <button
                  type="button"
                  className="btn-ghost shrink-0"
                  onClick={() => setRevoking(key)}
                  title="Stop this key working. It cannot be restored."
                >
                  Revoke
                </button>
              )}
            </li>
          ))}
        </ul>
      )}

      <p className="mt-3 border-t border-line-soft pt-3 text-small text-dim">
        Send a key as <span className="figure">X-API-Key</span> or{' '}
        <span className="figure">Authorization: Bearer</span>, in a header and never in a
        URL. Whatever its access, a key cannot make keys, change your password, reveal a
        stored game key or type your saved sign-in details: those need you signed in
        here. The{' '}
        <a href="/api/docs" className="text-accent underline-offset-2 hover:underline">
          API reference
        </a>{' '}
        lists the rest.
      </p>

      <NewKeyDialog open={creating} onClose={() => setCreating(false)} />

      <ConfirmDialog
        open={revoking !== null}
        onClose={() => setRevoking(null)}
        onConfirm={() => revoking && revoke.mutate(revoking.id)}
        title={revoking ? `Revoke ${revoking.name}?` : 'Revoke the key?'}
        consequence="Anything using this key stops working at once, and the key cannot be brought back. Make a new one if it is still needed."
        confirmLabel="Revoke"
        busy={revoke.isPending}
      />
    </Panel>
  )
}

function NewKeyDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const queryClient = useQueryClient()
  const { push } = useToast()
  const [name, setName] = useState('')
  const [scope, setScope] = useState<ApiKeyScope>('full')
  // The plaintext exists only in this response. It is held here until the
  // person closes the dialog on purpose, and dropped the moment they do.
  const [issued, setIssued] = useState<ApiKeyCreated | null>(null)

  useEffect(() => {
    if (!open) {
      setName('')
      setScope('full')
      setIssued(null)
    }
  }, [open])

  const create = useMutation({
    mutationFn: () => api.apiKeys.create(name.trim(), scope),
    onSuccess: (key) => {
      setIssued(key)
      void queryClient.invalidateQueries({ queryKey: ['api-keys'] })
    },
    onError: (error: Error) => push(error.message, 'critical'),
  })

  if (issued) {
    return (
      <Dialog
        open={open}
        onClose={onClose}
        title="Copy the key now"
        subtitle="Trove keeps only a fingerprint of it, so this is the one time it can be shown."
        size="small"
        footer={
          <button type="button" className="btn-primary" onClick={onClose}>
            I have copied it
          </button>
        }
      >
        <div className="flex flex-col gap-3">
          {/* A textarea, so the whole key is readable at phone width, and
              focus selects it all for copying by hand where the clipboard is
              refused (plain HTTP, which is how a self-hosted Trove is often
              reached). */}
          <textarea
            readOnly
            rows={2}
            value={issued.key}
            aria-label="Your new API key"
            className="field figure h-auto resize-none break-all py-1.5 text-small"
            onFocus={(event) => event.currentTarget.select()}
          />
          <div className="flex items-center gap-2">
            <CopyButton value={issued.key} label="Copy the key" />
            <span className="text-small text-dim">
              {issued.name} &middot; {SCOPE_LABEL[issued.scope]}
            </span>
          </div>
        </div>
      </Dialog>
    )
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="New API key"
      subtitle="Name it after whatever will use it, so you know which one to revoke."
      size="small"
      busy={create.isPending ? 'Making the key.' : undefined}
      footer={
        <>
          <button type="button" className="btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn-primary"
            disabled={!name.trim() || create.isPending}
            onClick={() => create.mutate()}
            title={name.trim() ? 'Make the key.' : 'Give the key a name first.'}
          >
            {create.isPending && <Spinner />}
            Make the key
          </button>
        </>
      }
    >
      <form
        className="flex flex-col gap-4"
        onSubmit={(event) => {
          event.preventDefault()
          if (name.trim() && !create.isPending) create.mutate()
        }}
      >
        <Field label="What is it for?">
          <input
            className="field"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Claude, Home Assistant..."
            maxLength={120}
            autoComplete="off"
          />
        </Field>
        <div className="flex flex-col gap-1">
          <div className="flex items-center justify-between gap-4">
            <span className="text-control text-fg">Access</span>
            <Segmented options={SCOPES} value={scope} onChange={setScope} label="Key access" />
          </div>
          <p className="text-small text-dim">{SCOPE_HELP[scope]}</p>
        </div>
      </form>
    </Dialog>
  )
}

/*
 * What the browser Trove drives actually looks like, from inside a page.
 *
 * Every challenge that has beaten this app was explained by one value the
 * browser reported - the codec list, the brand list, the WebGPU adapter - and
 * every one of them was invisible until somebody printed it. This is that
 * print statement with a button on it. It launches a throwaway profile exactly
 * the way a claim run does, so what it reports is what a store sees.
 *
 * It is not a score, on purpose. "Would the store let this through" has no
 * answer short of asking the store, and a number would invite tuning to the
 * number. What it shows is the contradictions worth a sentence.
 */
function BrowserCheck() {
  const { push } = useToast()
  const [report, setReport] = useState<BrowserDiagnostics | null>(null)
  const check = useMutation({
    mutationFn: api.diagnostics.browser,
    onSuccess: setReport,
    onError: (error: Error) => push(error.message, 'critical'),
  })

  const page = (report?.page ?? {}) as Record<string, any>
  const row = (label: string, value: unknown) => (
    <div className="contents">
      <dt className="text-small text-dim">{label}</dt>
      <dd className="min-w-0 break-words font-mono text-small text-fg">
        {value == null || value === '' ? <span className="text-dim">&ndash;</span> : String(value)}
      </dd>
    </div>
  )
  const webgl = page.webgl && typeof page.webgl === 'object' ? page.webgl : null
  const webgpu = page.webgpu && typeof page.webgpu === 'object' ? page.webgpu : null
  const codecs = page.codecs && typeof page.codecs === 'object' ? page.codecs : null

  return (
    <Panel
      title="The browser"
      className="mt-4"
      commands={
        <button
          type="button"
          className="btn-secondary"
          onClick={() => check.mutate()}
          disabled={check.isPending}
          title="Launch the browser the way a run does and report what a page sees. Takes a few seconds."
        >
          {check.isPending ? <Spinner /> : <Stethoscope className="size-icon" />}
          {report ? 'Check again' : 'Check the browser'}
        </button>
      }
    >
      {!report && !check.isPending && (
        <p className="text-small text-dim">
          Opens a throwaway browser exactly the way a claim run does and reports
          what a store page would see: which Chrome, which codecs, whether WebGL
          and WebGPU exist. A store&rsquo;s challenge reads those before it reads
          anything you click, and each of them has been the reason one failed.
        </p>
      )}
      {check.isPending && (
        <p className="flex items-center gap-2 text-small text-dim">
          <Spinner /> Launching the browser. This takes a few seconds, longer the first time.
        </p>
      )}
      {report && !check.isPending && (
        <div className="grid gap-4">
          {report.findings.length === 0 ? (
            <p className="flex items-center gap-2 text-small">
              <StatusBadge tone="good" label="Nothing stood out" />
              <span className="text-dim">
                Real Chrome, the codecs, WebGL and WebGPU are all there.
              </span>
            </p>
          ) : (
            <ul className="grid gap-2">
              {report.findings.map((finding, index) => (
                <li key={index} className="flex items-start gap-2 text-small">
                  <StatusBadge
                    tone={
                      finding.level === 'critical'
                        ? 'critical'
                        : finding.level === 'caution'
                          ? 'caution'
                          : 'neutral'
                    }
                    label={finding.level}
                  />
                  <span>{finding.text}</span>
                </li>
              ))}
            </ul>
          )}
          <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-4 gap-y-1">
            {row('Browser', report.browser_version ?? report.channel)}
            {row('User agent', page.user_agent)}
            {row('Brands', Array.isArray(page.brands) ? page.brands.join(', ') : page.brands)}
            {row('Platform', page.platform)}
            {row(
              'Codecs',
              codecs
                ? Object.entries(codecs)
                    .map(([name, value]) => `${name}: ${value || 'no'}`)
                    .join('  ')
                : page.codecs,
            )}
            {row('WebGL', webgl ? `${webgl.renderer}` : page.webgl === null ? 'none' : page.webgl)}
            {row(
              'WebGPU',
              webgpu
                ? webgpu.adapter
                  ? `adapter: ${[webgpu.vendor, webgpu.architecture, webgpu.description].filter(Boolean).join(' ') || 'yes'}`
                  : 'no adapter'
                : page.webgpu === null
                  ? 'unavailable'
                  : page.webgpu,
            )}
            {row(
              'Screen',
              page.screen ? `${page.screen.width}x${page.screen.height} @${page.screen.dpr}x` : null,
            )}
            {row('Focus', page.has_focus)}
            {row('Timezone', page.timezone)}
            {row('Where', `${report.in_container ? 'container' : 'desktop'}${report.headless ? ', headless' : ''}${report.display ? `, display ${report.display}` : ''}${report.vnc ? `, VNC ${report.vnc}` : ''}`)}
            {row('Launch flags', report.launch_args.join(' '))}
            {row('Took', `${report.seconds} s`)}
          </dl>
        </div>
      )}
    </Panel>
  )
}

function PasswordForm({
  busy,
  onSubmit,
}: {
  busy: boolean
  onSubmit: (current: string, next: string) => void
}) {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [again, setAgain] = useState('')

  const mismatch = again.length > 0 && next !== again
  const tooShort = next.length > 0 && next.length < 8

  return (
    <form
      className="grid gap-3 sm:grid-cols-3"
      onSubmit={(event) => {
        event.preventDefault()
        if (mismatch || tooShort || !current || !next) return
        onSubmit(current, next)
        setCurrent('')
        setNext('')
        setAgain('')
      }}
    >
      <Field label="Current password">
        <input
          type="password"
          className="field"
          value={current}
          onChange={(event) => setCurrent(event.target.value)}
          autoComplete="current-password"
        />
      </Field>
      <Field label="New password" error={tooShort ? 'Use at least eight characters.' : null}>
        <input
          type="password"
          className="field"
          value={next}
          onChange={(event) => setNext(event.target.value)}
          autoComplete="new-password"
        />
      </Field>
      <Field label="New password again" error={mismatch ? 'These two do not match.' : null}>
        <input
          type="password"
          className="field"
          value={again}
          onChange={(event) => setAgain(event.target.value)}
          autoComplete="new-password"
        />
      </Field>
      <div className="sm:col-span-3">
        <button
          type="submit"
          className="btn-secondary"
          disabled={busy || mismatch || tooShort || !current || !next}
        >
          {busy && <Spinner />}
          Change the password
        </button>
      </div>
    </form>
  )
}
