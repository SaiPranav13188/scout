"use client"

import { ArrowRight, Loader2, Lock, Mail, TriangleAlert } from "lucide-react"
import { motion, useAnimationControls } from "motion/react"
import { useRouter } from "next/navigation"
import { useState } from "react"

import { Aurora } from "@/components/aurora"
import { Logo } from "@/components/logo"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { getSupabase, supabaseConfigured } from "@/lib/supabase"

const container = {
  hidden: {},
  show: { transition: { staggerChildren: 0.07, delayChildren: 0.15 } },
}
const item = {
  hidden: { opacity: 0, y: 14, filter: "blur(6px)" },
  show: { opacity: 1, y: 0, filter: "blur(0px)", transition: { type: "spring" as const, stiffness: 260, damping: 26 } },
}

export default function LoginPage() {
  const router = useRouter()
  const shake = useAnimationControls()
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)
  const configured = supabaseConfigured()

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setPending(true)
    const { error } = await getSupabase().auth.signInWithPassword({ email, password })
    setPending(false)
    if (error) {
      setError(error.message)
      shake.start({ x: [0, -10, 10, -6, 6, -2, 0], transition: { duration: 0.45 } })
      return
    }
    router.replace("/")
  }

  return (
    <main className="relative grid min-h-dvh place-items-center overflow-hidden px-4">
      <Aurora />
      <motion.div variants={container} initial="hidden" animate="show" className="relative z-10 w-full max-w-sm">
        <motion.div variants={item} className="mb-8 flex flex-col items-center text-center">
          <motion.div whileHover={{ rotate: 90 }} transition={{ type: "spring", stiffness: 200, damping: 12 }}>
            <Logo className="size-12 drop-shadow-[0_8px_30px_var(--glow)]" />
          </motion.div>
          <h1 className="text-gradient mt-5 text-3xl font-semibold tracking-tight">Welcome back</h1>
          <p className="mt-2 text-sm text-muted-foreground">Fresh entry-level roles, found while you slept.</p>
        </motion.div>

        <motion.div variants={item} animate={shake}>
          <div className="rounded-2xl border bg-card/60 p-6 shadow-2xl shadow-black/40 backdrop-blur-xl">
            {!configured ? (
              <div className="flex gap-3 text-sm">
                <TriangleAlert className="mt-0.5 size-4 shrink-0 text-warning" />
                <div>
                  <p className="font-medium">Sign-in isn&apos;t set up yet</p>
                  <p className="mt-1 text-muted-foreground">
                    Add <code className="font-mono text-xs">NEXT_PUBLIC_SUPABASE_URL</code> and{" "}
                    <code className="font-mono text-xs">NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY</code> to{" "}
                    <code className="font-mono text-xs">apps/web/.env.local</code>. Steps are in docs/SETUP.md.
                  </p>
                </div>
              </div>
            ) : (
              <form onSubmit={onSubmit} className="space-y-4">
                <div className="space-y-2">
                  <Label htmlFor="email">Email</Label>
                  <div className="relative">
                    <Mail className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
                    <Input id="email" type="email" autoComplete="email" required value={email}
                      onChange={(e) => setEmail(e.target.value)} className="h-10 pl-9" placeholder="you@example.com" />
                  </div>
                </div>
                <div className="space-y-2">
                  <Label htmlFor="password">Password</Label>
                  <div className="relative">
                    <Lock className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
                    <Input id="password" type="password" autoComplete="current-password" required value={password}
                      onChange={(e) => setPassword(e.target.value)} className="h-10 pl-9" />
                  </div>
                </div>
                {error && (
                  <motion.p initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }}
                    className="text-sm text-danger" role="alert">
                    {error}
                  </motion.p>
                )}
                <Button type="submit" disabled={pending}
                  className="group h-10 w-full bg-brand text-brand-foreground hover:bg-brand/90">
                  {pending ? <Loader2 className="animate-spin" /> : (
                    <>
                      Sign in
                      <ArrowRight className="transition-transform group-hover:translate-x-0.5" />
                    </>
                  )}
                </Button>
              </form>
            )}
          </div>
        </motion.div>
        <motion.p variants={item} className="mt-6 text-center text-xs text-muted-foreground">
          Private dashboard · single owner
        </motion.p>
      </motion.div>
    </main>
  )
}
