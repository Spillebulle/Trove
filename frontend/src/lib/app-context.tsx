/*
 * Two things every page needs: who is signed in, and the theme.
 *
 * Both are here rather than in a store, because both are one value and a
 * context is what React already has for one value.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from './api'
import type { AuthStatus } from './types'

/* ── Theme ────────────────────────────────────────────────────────────────
 *
 * Three states (STYLE-GUIDE 3.1): dark, light, and follow the system. The
 * third stamps *nothing* on `<html>`, so `prefers-color-scheme` decides and
 * `tokens.css` handles all three without a component ever reading the class.
 *
 * The same three lines run in `index.html` before first paint. The two have to
 * agree exactly, or the first frame shows the other theme and then swaps.
 */

export type ThemeChoice = 'dark' | 'light' | 'system'

const THEME_KEY = 'trove.theme'

export function applyTheme(choice: ThemeChoice): void {
  const root = document.documentElement
  root.classList.remove('dark', 'light')
  if (choice === 'dark' || choice === 'light') root.classList.add(choice)
}

interface AppValue {
  auth: AuthStatus | undefined
  authLoading: boolean
  signIn: (username: string, password: string) => Promise<void>
  signOut: () => Promise<void>
  theme: ThemeChoice
  setTheme: (choice: ThemeChoice) => void
}

const AppContext = createContext<AppValue | null>(null)

export function AppProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()

  const { data: auth, isLoading: authLoading } = useQuery({
    queryKey: ['auth'],
    queryFn: api.auth.status,
    // The session outlives a page, so re-asking on every window focus buys
    // nothing. An expired cookie surfaces as the 401 on the next real request.
    staleTime: 60_000,
    retry: false,
  })

  const [theme, setThemeState] = useState<ThemeChoice>(() => {
    const stored = localStorage.getItem(THEME_KEY)
    return stored === 'light' || stored === 'system' ? stored : 'dark'
  })

  useEffect(() => {
    applyTheme(theme)
  }, [theme])

  const setTheme = useCallback((choice: ThemeChoice) => {
    localStorage.setItem(THEME_KEY, choice)
    setThemeState(choice)
  }, [])

  const signIn = useCallback(
    async (username: string, password: string) => {
      const status = await api.auth.login(username, password)
      queryClient.setQueryData(['auth'], status)
      // Every page's data was fetched, or refused, as nobody. Drop all of it.
      await queryClient.invalidateQueries()
    },
    [queryClient],
  )

  const signOut = useCallback(async () => {
    await api.auth.logout()
    queryClient.setQueryData(['auth'], { authenticated: false, username: null })
    // Clear rather than invalidate: an invalidated query refetches, and every
    // refetch after a sign-out is a 401 the user did not ask for.
    queryClient.clear()
  }, [queryClient])

  const value = useMemo(
    () => ({ auth, authLoading, signIn, signOut, theme, setTheme }),
    [auth, authLoading, signIn, signOut, theme, setTheme],
  )

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>
}

export function useApp(): AppValue {
  const value = useContext(AppContext)
  if (!value) throw new Error('useApp must be used inside AppProvider')
  return value
}

/* ── Toasts ───────────────────────────────────────────────────────────────
 *
 * One sentence, at most one action, gone in six seconds unless it reports an
 * error, which stays until dismissed (7.17). Never more than three on screen.
 */

export interface Toast {
  id: number
  message: string
  tone: 'good' | 'critical' | 'neutral'
}

interface ToastValue {
  toasts: Toast[]
  push: (message: string, tone?: Toast['tone']) => void
  dismiss: (id: number) => void
}

const ToastContext = createContext<ToastValue | null>(null)

const TOAST_LIFETIME_MS = 6000
const MAX_TOASTS = 3

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])

  const dismiss = useCallback((id: number) => {
    setToasts((current) => current.filter((toast) => toast.id !== id))
  }, [])

  const push = useCallback(
    (message: string, tone: Toast['tone'] = 'neutral') => {
      const id = Date.now() + Math.random()
      setToasts((current) => [...current, { id, message, tone }].slice(-MAX_TOASTS))
      // An error stays until it is dismissed: it is the one kind of message
      // that is worth reading twice, and it usually needs acting on.
      if (tone !== 'critical') {
        window.setTimeout(() => dismiss(id), TOAST_LIFETIME_MS)
      }
    },
    [dismiss],
  )

  const value = useMemo(() => ({ toasts, push, dismiss }), [toasts, push, dismiss])
  return <ToastContext.Provider value={value}>{children}</ToastContext.Provider>
}

export function useToast(): ToastValue {
  const value = useContext(ToastContext)
  if (!value) throw new Error('useToast must be used inside ToastProvider')
  return value
}
