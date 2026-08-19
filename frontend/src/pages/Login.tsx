/*
 * Signing in to Trove itself.
 *
 * Not a store: this is the one password Trove has, and it guards live sessions
 * for every store account, which is why the page says so rather than being a
 * bare pair of fields.
 */
import { useState } from 'react'
import { useApp } from '@/lib/app-context'
import { Wordmark } from '@/components/Brand'
import { Field, Spinner } from '@/components/ui'

export function Login() {
  const { signIn } = useApp()
  const [username, setUsername] = useState('admin')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  return (
    <div className="grid min-h-screen place-items-center bg-backdrop px-strip">
      <div className="w-full max-w-[380px]">
        <div className="mb-5 flex justify-center">
          <Wordmark />
        </div>

        <form
          className="card flex flex-col gap-4 p-5"
          onSubmit={async (event) => {
            event.preventDefault()
            setBusy(true)
            setError(null)
            try {
              await signIn(username, password)
            } catch (exc) {
              setError(exc instanceof Error ? exc.message : 'That did not work.')
            } finally {
              setBusy(false)
            }
          }}
        >
          <Field label="Username">
            <input
              className="field"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              autoComplete="username"
              autoFocus
            />
          </Field>

          <Field label="Password" error={error}>
            <input
              type="password"
              className="field"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete="current-password"
            />
          </Field>

          <button type="submit" className="btn-primary" disabled={busy || !password}>
            {busy && <Spinner />}
            Sign in
          </button>
        </form>

        <p className="mt-4 text-center text-small text-dim">
          On a fresh install this is <span className="keycap">admin</span> and{' '}
          <span className="keycap">changeme</span>, unless you set{' '}
          <span className="keycap">ADMIN_PASSWORD</span>. Change it: this
          password guards signed-in sessions for every store account.
        </p>
      </div>
    </div>
  )
}
