/*
 * The accounts list, and adding one.
 *
 * An account is a card rather than a row: it carries a status, a reason, an
 * interval and two actions, which is more than a 32px row holds without
 * becoming a table nobody can read on a phone.
 */
import { useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Users } from 'lucide-react'
import { api } from '@/lib/api'
import { useToast } from '@/lib/app-context'
import {
  ACCOUNT_STATUS_LABEL,
  ACCOUNT_STATUS_TONE,
  claimedSplit,
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
  StoreBadge,
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
                    {/* The store's own mark beside its name, so two accounts on
                        different stores tell apart before the words are read. */}
                    <p className="flex items-center gap-1.5 text-small text-dim">
                      <StoreBadge store={account.store} withName />
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
                    {/* The split only when there is one: "3" with "3 games, 0
                        add-ons" under it is a line that says nothing twice. */}
                    {account.claimed_dlc_count > 0 && (
                      <dd className="mt-0.5 text-tiny text-dim">
                        {claimedSplit(account.claimed_count, account.claimed_dlc_count)}
                      </dd>
                    )}
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
  const nameRef = useRef<HTMLInputElement>(null)
  // A missing name is said, not just implied by a greyed-out button: a
  // disabled Add with no reason was a dead end. Pressing Add with no name
  // scrolls to the field, outlines it and says what it wants.
  const [nameMissing, setNameMissing] = useState(false)

  const close = () => {
    setNameMissing(false)
    onClose()
  }

  const submit = () => {
    if (!label.trim()) {
      setNameMissing(true)
      nameRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })
      nameRef.current?.focus({ preventScroll: true })
      return
    }
    create.mutate()
  }

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
      onClose={close}
      title="Add an account"
      subtitle="Name it, then pick its store."
      size="small"
      footer={
        <>
          <button type="button" className="btn-ghost" onClick={close}>
            Cancel
          </button>
          <button
            type="button"
            className="btn-primary"
            disabled={!activeStore || create.isPending}
            onClick={submit}
          >
            {create.isPending && <Spinner />}
            Add
          </button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        {/*
         * The name comes first. It is the only thing on this form the person
         * has to compose rather than pick, and with five stores the card list
         * below is tall enough that a name field under it reads as an
         * afterthought - or is scrolled off altogether. First also means the
         * dialog's own "focus the first control" lands in it, so the form
         * opens ready to type.
         */}
        <Field
          label="Name"
          hint="Only for you, to tell accounts apart."
          error={nameMissing ? 'Give the account a name first.' : error}
        >
          <input
            ref={nameRef}
            className={'field' + (nameMissing ? ' border-critical focus:border-critical' : '')}
            aria-invalid={nameMissing || undefined}
            value={label}
            onChange={(event) => {
              setLabel(event.target.value)
              if (event.target.value.trim()) setNameMissing(false)
            }}
            onKeyDown={(event) => {
              if (event.key === 'Enter') submit()
            }}
            placeholder="Main Epic account"
            maxLength={120}
          />
        </Field>

        {/*
         * Cards rather than a dropdown, because each store carries its mark and
         * a few words, and a dropdown has nowhere to put either. Written
         * as a grid from the start, which is why going from one adapter to
         * five needed no layout change here.
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
                <span className="flex items-center gap-3">
                  <input
                    type="radio"
                    name="store"
                    className="sr-only"
                    checked={item.store === activeStore}
                    onChange={() => setStore(item.store)}
                  />
                  <span className="text-fg">
                    <StoreBadge store={item.store} />
                  </span>
                  <span className="min-w-0">
                    <span className="block text-control font-semibold text-strong">
                      {item.display_name}
                    </span>
                    <span className="block truncate text-small text-dim">{item.tagline}</span>
                  </span>
                </span>
              </label>
            ))}
          </div>
        </fieldset>

        {/*
         * Short by default. The store's full story - what it claims, what it
         * cannot, and the plain word on its terms - is one click in rather
         * than a wall of text on every card. The requirement names stay
         * visible, because "Accepting the risk" should be seen before Add.
         */}
        {chosen && (
          <div className="well p-3">
            {chosen.requirements.length > 0 && (
              <>
                <p className="eyebrow mb-1">What this needs</p>
                <ul className="flex flex-wrap gap-x-3 gap-y-1 text-small text-fg">
                  {chosen.requirements.map((requirement) => (
                    <li key={requirement.name}>
                      {requirement.name}
                      {!requirement.required && <span className="ml-1 text-tiny text-dim">optional</span>}
                    </li>
                  ))}
                </ul>
              </>
            )}
            <details className="mt-2 text-small text-dim">
              <summary className="cursor-pointer text-accent hover:brightness-110">
                More about {chosen.display_name}
              </summary>
              <p className="mt-2">{chosen.blurb}</p>
              <ul className="mt-2 flex flex-col gap-1.5">
                {chosen.requirements.map((requirement) => (
                  <li key={requirement.name}>
                    <span className="text-fg">{requirement.name}:</span> {requirement.description}
                  </li>
                ))}
              </ul>
            </details>
          </div>
        )}
      </div>
    </Dialog>
  )
}
