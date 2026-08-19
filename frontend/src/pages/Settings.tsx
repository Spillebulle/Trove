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
import { Send } from 'lucide-react'
import { api } from '@/lib/api'
import { useApp, useToast, type ThemeChoice } from '@/lib/app-context'
import { REDACTED } from '@/lib/types'
import { ErrorState, Field, PageHeader, Panel, Segmented, Spinner, Toggle } from '@/components/ui'

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

      <p className="mt-6 text-small text-dim">
        Trove signs in to stores you already have accounts with, on your own
        machine. Automating a store login can breach that store&rsquo;s terms of
        service, and that is your call to make rather than the app&rsquo;s.
      </p>
    </>
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
