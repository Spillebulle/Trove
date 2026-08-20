/*
 * The hosted web shell: STYLE-GUIDE 6.2, and nothing more.
 *
 *   Top bar   52px, `chrome`, hairline below. The 22px accent mark and the app
 *             name at the left; the scheduler's state and the session control
 *             at the right. This bar is the menu bar. It holds no page
 *             navigation and it never grows to hold a search field.
 *   Sidebar   280px, `dock`, hairline right. Section eyebrows, 38px nav rows
 *             with the 3px accent bar when selected, and a footer with the
 *             version and the licence.
 *   Content   over `window`, capped at 1200px. A page is a panel interior; the
 *             `backdrop` pit is for a canvas, and Trove has none.
 *
 * **Below 1024px the sidebar becomes a drawer, not bar navigation.** Bar
 * navigation is offered for five destinations or fewer (6.3) and Trove has
 * five exactly - which makes it the closer call than it looks. The drawer wins
 * because two of the five (Settings, and the attention count on Accounts) are
 * things a person visits deliberately rather than switches between, and a
 * bottom bar is for switching. It can be revisited if the app ever loses a
 * destination.
 *
 * **One rule for tooltips**, applied throughout: a `title` is a sentence and
 * takes a full stop (12), and its `aria-label` carries the same string, so a
 * person hovering and a person listening are told the same thing.
 */
import { useEffect, useState } from 'react'
import { Link, NavLink, Outlet, useLocation } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  Clock,
  Gift,
  LayoutDashboard,
  LogOut,
  Menu,
  Settings as SettingsIcon,
  Users,
  X,
  type LucideIcon,
} from 'lucide-react'
import { api } from '@/lib/api'
import { useApp, useToast } from '@/lib/app-context'
import { cn } from '@/lib/utils'
import { Mark } from './Brand'

interface NavItem {
  to: string
  label: string
  icon: LucideIcon
  end?: boolean
}

const NAV_GROUPS: Array<{ eyebrow?: string; items: NavItem[] }> = [
  { items: [{ to: '/', label: 'Overview', icon: LayoutDashboard, end: true }] },
  {
    eyebrow: 'Claiming',
    items: [
      { to: '/accounts', label: 'Accounts', icon: Users },
      { to: '/offers', label: 'Free now', icon: Gift },
      { to: '/ledger', label: 'Ledger', icon: Clock },
    ],
  },
]

const SETTINGS_ITEM: NavItem = { to: '/settings', label: 'Settings', icon: SettingsIcon }

function NavRow({ item, onNavigate }: { item: NavItem; onNavigate?: () => void }) {
  const Icon = item.icon
  return (
    <NavLink
      to={item.to}
      end={item.end}
      // In the drawer, tapping a row is the whole gesture, so it closes the
      // drawer. It has to: the row for the page already open changes no path,
      // and watching the path was the only other way out.
      onClick={onNavigate}
      className={({ isActive }) => cn('nav-row', isActive && 'nav-row-selected')}
    >
      {/* The icon takes the row's own colour, so it is `text-muted` at rest and
          `text-strong` when selected without being told twice. */}
      <Icon className="size-icon" aria-hidden="true" />
      {item.label}
    </NavLink>
  )
}

function AttentionCount() {
  const { data } = useQuery({
    queryKey: ['summary'],
    queryFn: api.summary,
    refetchInterval: 30_000,
  })
  const count = data?.accounts_needing_attention ?? 0
  if (!count) return null
  return (
    <span
      className="ml-auto figure text-tiny text-caution"
      title={`${count} account(s) are waiting for you.`}
    >
      {count}
    </span>
  )
}

function NavList({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <nav className="flex flex-col gap-px" aria-label="Main">
      {NAV_GROUPS.map((group, index) => (
        <div
          key={group.eyebrow ?? 'overview'}
          className={cn('flex flex-col gap-px', index > 0 && 'mt-3')}
        >
          {group.eyebrow && <p className="eyebrow px-strip pb-1">{group.eyebrow}</p>}
          {group.items.map((item) =>
            item.to === '/accounts' ? (
              <NavLink
                key={item.to}
                to={item.to}
                onClick={onNavigate}
                className={({ isActive }) => cn('nav-row', isActive && 'nav-row-selected')}
              >
                <item.icon className="size-icon" aria-hidden="true" />
                {item.label}
                <AttentionCount />
              </NavLink>
            ) : (
              <NavRow key={item.to} item={item} onNavigate={onNavigate} />
            ),
          )}
        </div>
      ))}
    </nav>
  )
}

/**
 * The version, read from the API rather than stated here.
 *
 * It used to be a `const VERSION = '0.1.0'` in this file, which is a second
 * source of truth and behaved exactly as a second source of truth does: the
 * backend went to 0.1.4 and the sidebar went on saying 0.1.0, so a correct
 * upgrade looked like a failed one. `/api/health` reports the build that is
 * actually serving the page, so the two cannot disagree, and a stale cached
 * bundle now reports the *server's* version rather than its own.
 *
 * There is one source of truth for the version and it is
 * `backend/app/__init__.py`, which the release workflow also checks against
 * the git tag.
 */
function SidebarFooter() {
  const { data } = useQuery({
    queryKey: ['health'],
    queryFn: api.health,
    staleTime: 5 * 60_000,
  })
  return (
    <div className="flex h-status shrink-0 items-center justify-between gap-2 border-t border-line px-strip">
      <span className="truncate text-tiny text-dim">
        {/* A version is read as a value, so it is monospaced and tabular. An
            en dash until it is known, never a guess. */}
        <span className="figure">{data ? `v${data.version}` : '–'}</span> · GPL-3.0
      </span>
    </div>
  )
}

/**
 * The scheduler's state, in the top bar.
 *
 * A sentence rather than a light: "Checking every few hours" and "Paused" are
 * two different things a person needs to know at a glance, and a green dot on
 * its own says only that something is on.
 */
function SchedulerState() {
  const { data } = useQuery({
    queryKey: ['summary'],
    queryFn: api.summary,
    refetchInterval: 30_000,
  })
  if (!data) return null
  const on = data.scheduler_enabled && data.scheduler_running
  return (
    <Link
      to="/settings"
      className="hidden items-center gap-1.5 text-tiny text-dim hover:text-fg sm:flex"
      title={
        on
          ? 'Trove is checking your accounts on a schedule. Open Settings.'
          : 'The schedule is paused, so nothing runs on its own. Open Settings.'
      }
    >
      <span
        aria-hidden="true"
        className={cn('h-1.5 w-1.5 rounded-full', on ? 'bg-good' : 'bg-dim')}
      />
      {on ? 'Scheduled' : 'Paused'}
    </Link>
  )
}

function Toasts() {
  const { toasts, dismiss } = useToast()
  if (!toasts.length) return null
  return (
    <div className="fixed bottom-4 right-4 z-40 flex flex-col gap-2">
      {toasts.map((toast) => (
        <div
          key={toast.id}
          role="status"
          className="floating flex max-w-sm items-start gap-3 px-3 py-2 animate-rise"
        >
          <span
            className={cn(
              'mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full',
              toast.tone === 'good' && 'bg-good',
              toast.tone === 'critical' && 'bg-critical',
              toast.tone === 'neutral' && 'bg-dim',
            )}
            aria-hidden="true"
          />
          <p className="min-w-0 flex-1 text-body text-fg">{toast.message}</p>
          <button
            type="button"
            onClick={() => dismiss(toast.id)}
            className="btn-icon h-5 w-5"
            title="Dismiss this."
            aria-label="Dismiss this."
          >
            <X size={14} />
          </button>
        </div>
      ))}
    </div>
  )
}

export function Layout() {
  const { auth, signOut } = useApp()
  const [drawerOpen, setDrawerOpen] = useState(false)
  const location = useLocation()

  // Escape closes the drawer, the same as it closes every other floating thing.
  useEffect(() => {
    if (!drawerOpen) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setDrawerOpen(false)
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [drawerOpen])

  // A route change closes the drawer even when the row was not what changed it
  // (a link inside the page, the back button).
  useEffect(() => {
    setDrawerOpen(false)
  }, [location.pathname])

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-window">
      <header className="z-30 flex h-menubar shrink-0 items-center gap-3 border-b border-line bg-chrome px-strip">
        <button
          type="button"
          className="btn-icon lg:hidden"
          onClick={() => setDrawerOpen((open) => !open)}
          title="Show the navigation."
          aria-label="Show the navigation."
          aria-expanded={drawerOpen}
        >
          <Menu className="size-icon" />
        </button>

        {/* The one place the app says who it is: the mark and the name, and
            nothing else. 6.2 is explicit that this bar does not grow to hold a
            strapline or a search field. */}
        <Link to="/" className="flex items-center gap-2.5" title="Trove overview.">
          <Mark />
          <span className="text-heading font-bold text-strong">Trove</span>
        </Link>

        <div className="ml-auto flex items-center gap-3">
          <SchedulerState />
          {auth?.authenticated && (
            <button
              type="button"
              className="btn-icon"
              onClick={() => void signOut()}
              title="Sign out of Trove."
              aria-label="Sign out of Trove."
            >
              <LogOut className="size-icon" />
            </button>
          )}
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        <aside className="hidden w-sidebar shrink-0 flex-col border-r border-line bg-dock lg:flex">
          <div className="flex-1 overflow-y-auto p-2">
            <NavList />
          </div>
          <div className="p-2 pt-0">
            <NavRow item={SETTINGS_ITEM} />
          </div>
          <SidebarFooter />
        </aside>

        {/* Below 1024px the same column is a drawer, drawn with the floating
            panel's styling (5) over a scrim. */}
        {drawerOpen && (
          <div className="fixed inset-0 z-40 lg:hidden">
            <div
              className="dialog-backdrop absolute inset-0"
              onClick={() => setDrawerOpen(false)}
              aria-hidden="true"
            />
            <aside className="floating absolute inset-y-0 left-0 flex w-sidebar flex-col rounded-l-none">
              <div className="flex h-menubar items-center gap-2.5 px-strip">
                <Mark />
                <span className="text-heading font-bold text-strong">Trove</span>
                <button
                  type="button"
                  className="btn-icon ml-auto"
                  onClick={() => setDrawerOpen(false)}
                  title="Close the navigation."
                  aria-label="Close the navigation."
                >
                  <X className="size-icon" />
                </button>
              </div>
              <div className="flex-1 overflow-y-auto p-2">
                <NavList onNavigate={() => setDrawerOpen(false)} />
              </div>
              <div className="p-2 pt-0">
                <NavRow item={SETTINGS_ITEM} onNavigate={() => setDrawerOpen(false)} />
              </div>
              <SidebarFooter />
            </aside>
          </div>
        )}

        <main className="min-w-0 flex-1 overflow-y-auto">
          <div className="mx-auto max-w-[1200px] px-strip py-5">
            <Outlet />
          </div>
        </main>
      </div>

      <Toasts />
    </div>
  )
}
