import { Navigate, Route, Routes } from 'react-router-dom'
import { useApp } from '@/lib/app-context'
import { Layout } from '@/components/Layout'
import { Spinner } from '@/components/ui'
import { AccountDetail } from '@/pages/AccountDetail'
import { Accounts } from '@/pages/Accounts'
import { Dashboard } from '@/pages/Dashboard'
import { Ledger } from '@/pages/Ledger'
import { Login } from '@/pages/Login'
import { Offers } from '@/pages/Offers'
import { Settings } from '@/pages/Settings'

export function App() {
  const { auth, authLoading } = useApp()

  // The first paint knows nothing about the session, and rendering the sign-in
  // page while the answer is in flight shows a login form to somebody who is
  // already signed in, once, on every reload.
  if (authLoading) {
    return (
      <div className="grid min-h-screen place-items-center bg-backdrop">
        <Spinner className="text-dim" size={20} />
      </div>
    )
  }

  if (!auth?.authenticated) {
    return <Login />
  }

  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Dashboard />} />
        <Route path="accounts" element={<Accounts />} />
        <Route path="accounts/:id" element={<AccountDetail />} />
        <Route path="offers" element={<Offers />} />
        <Route path="ledger" element={<Ledger />} />
        <Route path="settings" element={<Settings />} />
        {/* An unknown path is a typo or a stale bookmark, not an error worth a
            page of its own in an app this small. */}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}
