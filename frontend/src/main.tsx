import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { App } from './App'
import { AppProvider, ToastProvider } from './lib/app-context'
import './index.css'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // A self-hosted app on a LAN: refetching everything because a window
      // regained focus is a burst of requests nobody asked for. The pages that
      // genuinely change on their own set their own `refetchInterval`.
      refetchOnWindowFocus: false,
      staleTime: 10_000,
      // One retry, not three. When the API is down the useful thing is to say
      // so quickly; three silent attempts make a dead backend look like a slow
      // page.
      retry: 1,
    },
  },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <AppProvider>
        <ToastProvider>
          <BrowserRouter>
            <App />
          </BrowserRouter>
        </ToastProvider>
      </AppProvider>
    </QueryClientProvider>
  </StrictMode>,
)
