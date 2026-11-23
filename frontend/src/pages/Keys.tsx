/*
 * Keys to redeem.
 *
 * Some stores hand out a key instead of adding the game to a library - Prime
 * Gaming often does, and the bundle stores always do. Trove stores the key
 * encrypted and never redeems it: that is a click on the person's own store
 * account. This page is the to-do list for those clicks, with each key one
 * reveal away and, where the store allows it, a link that arrives with the key
 * already filled in.
 *
 * The list never carries a key. Revealing one is a request of its own, made
 * only by a signed-in session, and the value lives only as long as the row
 * that asked for it is open.
 */
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, ExternalLink, KeyRound, RotateCcw } from 'lucide-react'
import { api } from '@/lib/api'
import { useToast } from '@/lib/app-context'
import { redeemTarget } from '@/lib/redeem'
import type { Claim } from '@/lib/types'
import { fullTime, relativeTime } from '@/lib/utils'
import {
  Art,
  CopyButton,
  EmptyState,
  ErrorState,
  PageHeader,
  Segmented,
  Spinner,
  StatusBadge,
} from '@/components/ui'

type Filter = 'todo' | 'all'

const FILTERS: ReadonlyArray<{ value: Filter; label: string }> = [
  { value: 'todo', label: 'To redeem' },
  { value: 'all', label: 'All keys' },
]

export function Keys() {
  const [filter, setFilter] = useState<Filter>('todo')
  const keys = useQuery({ queryKey: ['redeem'], queryFn: api.claims.toRedeem })

  const rows = (keys.data ?? []).filter((c) => filter === 'all' || !c.key_redeemed_at)
  const todo = (keys.data ?? []).filter((c) => !c.key_redeemed_at).length

  return (
    <>
      <PageHeader
        title="Keys"
        subtitle={
          keys.data
            ? todo === 0
              ? 'Every key Trove has collected is redeemed.'
              : `${todo} ${todo === 1 ? 'key is' : 'keys are'} waiting to be redeemed on your own store account.`
            : 'Games that came as a key rather than a library add.'
        }
        actions={<Segmented options={FILTERS} value={filter} onChange={setFilter} label="Show" />}
      />

      <div className="panel">
        {keys.isError ? (
          <ErrorState error={keys.error} onRetry={() => void keys.refetch()} />
        ) : keys.isLoading ? (
          <div className="skeleton h-64" />
        ) : rows.length === 0 ? (
          <EmptyState
            icon={<KeyRound size={24} />}
            title={filter === 'todo' && (keys.data?.length ?? 0) > 0 ? 'Nothing left to redeem' : 'No keys yet'}
            description={
              filter === 'todo' && (keys.data?.length ?? 0) > 0
                ? 'Every key is marked redeemed. "All keys" shows them again.'
                : 'When a store hands out a key instead of adding the game to a library, it lands here to redeem.'
            }
          />
        ) : (
          <ul className="divide-y divide-line-soft">
            {rows.map((claim) => (
              <KeyRow key={claim.id} claim={claim} />
            ))}
          </ul>
        )}
      </div>
    </>
  )
}

function KeyRow({ claim }: { claim: Claim }) {
  const queryClient = useQueryClient()
  const { push } = useToast()
  const [shown, setShown] = useState(false)
  const target = redeemTarget(claim.key_store)
  const redeemed = Boolean(claim.key_redeemed_at)

  const key = useQuery({
    queryKey: ['claim-key', claim.id],
    queryFn: () => api.claims.key(claim.id),
    enabled: shown,
    // Never cached: the value should not outlive the row that asked for it.
    gcTime: 0,
    staleTime: 0,
    retry: false,
  })

  const mark = useMutation({
    mutationFn: (value: boolean) => api.claims.markRedeemed(claim.id, value),
    onSuccess: () => {
      setShown(false)
      void queryClient.invalidateQueries({ queryKey: ['redeem'] })
    },
    onError: (error: Error) => push(error.message, 'critical'),
  })

  const code = key.data?.key_code
  const url = code && target ? target.url(code) : null

  return (
    <li className="flex flex-col gap-3 px-strip py-3 sm:flex-row sm:items-start">
      <Art src={claim.image_url} store={claim.store} className="aspect-wide w-art-tile shrink-0" />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="truncate text-strong">{claim.title}</span>
          {redeemed ? (
            <StatusBadge tone="good" label="Redeemed" />
          ) : (
            <StatusBadge tone="neutral" label={target ? `For ${target.name}` : 'To redeem'} />
          )}
        </div>
        <p className="mt-0.5 text-small text-dim">
          {claim.account_label ?? 'A deleted account'} ·{' '}
          <span title={fullTime(claim.created_at)}>claimed {relativeTime(claim.created_at)}</span>
          {redeemed && (
            <span title={fullTime(claim.key_redeemed_at)}>
              {' '}
              · marked redeemed {relativeTime(claim.key_redeemed_at)}
            </span>
          )}
        </p>

        {shown && (
          <div className="mt-3">
            {key.isLoading ? (
              <span className="flex items-center gap-2 text-body text-dim">
                <Spinner />
                Reading the key.
              </span>
            ) : code ? (
              <>
                <div className="flex flex-wrap items-center gap-2">
                  {/* Selectable as well as copyable: clipboard access is
                      refused on a plain-HTTP page in some browsers. */}
                  <code className="well min-w-0 flex-1 select-all break-all p-3 font-mono text-control text-strong">
                    {code}
                  </code>
                  <CopyButton value={code} label="Copy" />
                </div>
                <p className="mt-2 text-small text-dim">
                  {target ? target.how : 'Redeem it on the store it is for; Trove did not record which.'}
                </p>
              </>
            ) : (
              <p className="text-body text-dim">
                {key.error instanceof Error ? key.error.message : 'The key could not be read.'}
              </p>
            )}
          </div>
        )}
      </div>

      <div className="flex shrink-0 flex-wrap items-center gap-2 sm:justify-end">
        {!shown ? (
          <button
            type="button"
            className={redeemed ? 'btn-ghost' : 'btn-secondary'}
            onClick={() => setShown(true)}
          >
            <KeyRound className="size-icon" />
            Show the key
          </button>
        ) : (
          url && (
            <a
              href={url}
              target="_blank"
              rel="noreferrer noopener"
              className="btn-primary"
              onClick={() => {
                // A page with no prefill needs the key pasted, so put it on
                // the clipboard on the way out. Best effort: a plain-HTTP page
                // may refuse, and the key is still on screen to copy.
                if (code && target && !target.prefilled) {
                  void navigator.clipboard?.writeText(code).catch(() => undefined)
                }
              }}
            >
              <ExternalLink className="size-icon" />
              Redeem on {target?.name}
            </a>
          )
        )}
        {redeemed ? (
          <button
            type="button"
            className="btn-ghost"
            disabled={mark.isPending}
            onClick={() => mark.mutate(false)}
            title="Put this key back on the to-redeem list."
          >
            <RotateCcw className="size-icon" />
            Not redeemed
          </button>
        ) : (
          <button
            type="button"
            className="btn-secondary"
            disabled={mark.isPending}
            onClick={() => mark.mutate(true)}
            title="Trove cannot see whether a store accepted a key, so this is your word for it."
          >
            {mark.isPending ? <Spinner /> : <Check className="size-icon" />}
            Mark redeemed
          </button>
        )}
      </div>
    </li>
  )
}
