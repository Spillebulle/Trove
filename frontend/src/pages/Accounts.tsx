/*
 * The accounts list, and adding one.
 *
 * An account is a card rather than a row: it carries a status, a reason, an
 * interval and two actions, which is more than a 32px row holds without
 * becoming a table nobody can read on a phone.
 */
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Users } from 'lucide-react'
import { api } from '@/lib/api'
import { useToast } from '@/lib/app-context'
import {
  ACCOUNT_STATUS_LABEL,
  ACCOUNT_STATUS_TONE,
  everyHours,
  fullTime,
  relativeTime,
} from '@/lib/utils'
import type { StoreInfo } from '@/lib/types'
import {
  Dialog,
  EmptyState,
  ErrorState,
  Field,
  PageHeader,
  Spinner,
  StatusBadge,
} from '@/components/ui'

export function Accounts() {
  const queryClient = useQueryClient()
  const { push } = useToast()
  const [adding, setAdding] = useState(false)

  const accounts = useQuery({ queryKey: ['accounts'], queryFn: api.accounts.list })
  const stores = useQuery({ queryKey: ['stores'], queryFn: api.accounts.stores })

  return (
    <>
      <PageHeader
        title="Accounts"
        subtitle="One store login each. Trove keeps the session, never the password."
        actions={
          <button type="button" className="btn-primary" onClick={() => setAdding(true)}>
            <Plus className="size-icon" />
            Add an account
          </button>
        }
      />

      {accounts.isError ? (
        <ErrorState error={accounts.error} onRetry={() => void accounts.refetch()} />
      ) : accounts.isLoading ? (
        <div className="grid gap-3 sm:grid-cols-2">
          {[0, 1].map((key) => (
            <div key={key} className="card skeleton h-32" />
          ))}
        </div>
      ) : (accounts.data?.length ?? 0) === 0 ? (
        <div className="card">
          <EmptyState
            icon={<Users size={24} />}
            title="No accounts yet"
            description="Add the store account you want Trove to claim on. You sign in once, by hand, and it reuses that session from then on."
            action={
              <button type="button" className="btn-secondary" onClick={() => setAdding(true)}>
                Add an account
              </button>
            }
          />
        </div>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2">
          {(accounts.data ?? []).map((account) => {
            const store = stores.data?.find((item) => item.store === account.store)
            return (
              <Link
                key={account.id}
                to={`/accounts/${account.id}`}
                className="card block p-strip transition-colors duration-hover ease-ease hover:bg-control-hover"
              >
                <div className="flex items-start gap-3">
                  <div className="min-w-0 flex-1">
                    <h2 className="truncate text-heading font-semibold text-strong">
                      {account.label}
                    </h2>
                    <p className="truncate text-small text-dim">
                      {store?.display_name ?? account.store}
                    </p>
                  </div>
                  <StatusBadge
                    tone={ACCOUNT_STATUS_TONE[account.status]}
                    label={ACCOUNT_STATUS_LABEL[account.status]}
                  />
                </div>

                {account.status_reason && (
                  <p className="mt-2 text-small text-caution">{account.status_reason}</p>
                )}

                <dl className="mt-3 grid grid-cols-3 gap-2 text-tiny">
                  <div>
                    <dt className="eyebrow">Claimed</dt>
                    <dd className="figure text-fg">{account.claimed_count}</dd>
                  </div>
                  <div>
                    <dt className="eyebrow">Last run</dt>
                    <dd className="text-fg" title={fullTime(account.last_run_at)}>
                      {relativeTime(account.last_run_at)}
                    </dd>
                  </div>
                  <div>
                    <dt className="eyebrow">Checks</dt>
                    <dd className="text-fg">
                      {account.enabled ? everyHours(account.effective_interval_hours) : 'Paused'}
                    </dd>
                  </div>
                </dl>
              </Link>
            )
          })}
        </div>
      )}

      <AddAccountDialog
        open={adding}
        onClose={() => setAdding(false)}
        stores={stores.data ?? []}
        onCreated={async (label) => {
          await queryClient.invalidateQueries({ queryKey: ['accounts'] })
          await queryClient.invalidateQueries({ queryKey: ['summary'] })
          push(`Added ${label}. Sign in to it to finish.`, 'good')
          setAdding(false)
        }}
      />
    </>
  )
}

function AddAccountDialog({
  open,
  onClose,
  stores,
  onCreated,
}: {
  open: boolean
  onClose: () => void
  stores: StoreInfo[]
  onCreated: (label: string) => void | Promise<void>
}) {
  const [store, setStore] = useState('')
  const [label, setLabel] = useState('')
  const [error, setError] = useState<string | null>(null)

  const chosen = stores.find((item) => item.store === store) ?? stores[0]
  const activeStore = store || stores[0]?.store || ''

  const create = useMutation({
    mutationFn: () => api.accounts.create({ store: activeStore, label: label.trim() }),
    onSuccess: async () => {
      const name = label.trim()
      setLabel('')
      setError(null)
      await onCreated(name)
    },
    onError: (err: Error) => setError(err.message),
  })

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Add an account"
      subtitle="Pick the store, give the account a name you will recognise."
      size="small"
      footer={
        <>
          <button type="button" className="btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn-primary"
            disabled={!label.trim() || !activeStore || create.isPending}
            onClick={() => create.mutate()}
          >
            {create.isPending && <Spinner />}
            Add
          </button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        {/*
         * Cards rather than a dropdown, because each store carries a sentence
         * about what it needs and a dropdown has nowhere to put one. With a
         * single adapter this is one card; it is written as a grid so the
         * second adapter needs no layout change.
         */}
        <fieldset>
          <legend className="mb-1 text-control text-fg">Store</legend>
          <div className="grid gap-2">
            {stores.map((item) => (
              <label
                key={item.store}
                className={
                  'card cursor-pointer p-3 transition-colors duration-hover ' +
                  (item.store === activeStore
                    ? 'border-2 border-accent'
                    : 'hover:border-line-dashed')
                }
              >
                <span className="flex items-start gap-2">
                  <input
                    type="radio"
                    name="store"
                    className="sr-only"
                    checked={item.store === activeStore}
                    onChange={() => setStore(item.store)}
                  />
                  <span className="min-w-0">
                    <span className="block text-control font-semibold text-strong">
                      {item.display_name}
                    </span>
                    <span className="mt-0.5 block text-small text-dim">{item.blurb}</span>
                  </span>
                </span>
              </label>
            ))}
          </div>
        </fieldset>

        <Field
          label="Name"
          hint="Only for you. Two accounts on the same store need telling apart."
          error={error}
        >
          <input
            className="field"
            value={label}
            onChange={(event) => setLabel(event.target.value)}
            placeholder="Main Epic account"
            maxLength={120}
          />
        </Field>

        {chosen && chosen.requirements.length > 0 && (
          <div className="well p-3">
            <p className="eyebrow mb-2">What this needs</p>
            <ul className="flex flex-col gap-2">
              {chosen.requirements.map((requirement) => (
                <li key={requirement.name}>
                  <span className="block text-control text-fg">
                    {requirement.name}
                    {!requirement.required && (
                      <span className="ml-1.5 text-tiny text-dim">optional</span>
                    )}
                  </span>
                  <span className="block text-small text-dim">{requirement.description}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </Dialog>
  )
}
