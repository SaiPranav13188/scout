"use client"

import type { Session } from "@supabase/supabase-js"
import { motion } from "motion/react"
import { useRouter } from "next/navigation"
import { createContext, useContext, useEffect, useState, useSyncExternalStore } from "react"

import { Logo } from "@/components/logo"
import { devAuthToken, getSupabase, supabaseConfigured } from "@/lib/supabase"

const SessionContext = createContext<Session | null>(null)
const noopSubscribe = () => () => {}
export const useSession = () => useContext(SessionContext)

/** Client-side gate. The API independently verifies the token on every request. */
export function AuthGate({ children }: { children: React.ReactNode }) {
  const router = useRouter()
  const [session, setSession] = useState<Session | null | undefined>(undefined)
  const dev = devAuthToken() !== null

  useEffect(() => {
    if (dev) return
    if (!supabaseConfigured()) {
      router.replace("/login")
      return
    }
    const sb = getSupabase()
    sb.auth.getSession().then(({ data }) => {
      setSession(data.session)
      if (!data.session) router.replace("/login")
    })
    const { data: sub } = sb.auth.onAuthStateChange((_e, s) => {
      setSession(s)
      if (!s) router.replace("/login")
    })
    return () => sub.subscription.unsubscribe()
  }, [router, dev])

  // Dashboard content is client-only (all data loads in the browser). Rendering it on the server
  // would let data that arrives during hydration mismatch the server HTML.
  const mounted = useSyncExternalStore(noopSubscribe, () => true, () => false)
  if (dev && mounted) return <SessionContext.Provider value={null}>{children}</SessionContext.Provider>
  if (!session) {
    return (
      <div className="grid min-h-dvh place-items-center">
        <motion.div
          initial={{ opacity: 0, scale: 0.9 }}
          animate={{ opacity: 1, scale: 1 }}
          className="flex flex-col items-center gap-4"
        >
          <motion.div animate={{ rotate: 360 }} transition={{ repeat: Infinity, duration: 2.4, ease: "linear" }}>
            <Logo className="size-9" />
          </motion.div>
          <p className="text-sm text-muted-foreground">Checking your session…</p>
        </motion.div>
      </div>
    )
  }
  return <SessionContext.Provider value={session}>{children}</SessionContext.Provider>
}
