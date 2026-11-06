"use client"

import { LogOut, Moon, Search, Sun } from "lucide-react"
import { AnimatePresence, motion } from "motion/react"
import { usePathname } from "next/navigation"
import { useSyncExternalStore } from "react"

import { useSession } from "@/components/auth-gate"
import { PulseDot } from "@/components/motion"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Kbd } from "@/components/ui/kbd"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { hoursSince, timeAgo } from "@/lib/format"
import { useStats } from "@/lib/queries"
import { getSupabase } from "@/lib/supabase"

import { NAV } from "./nav"

function subscribeTheme(cb: () => void) {
  const mo = new MutationObserver(cb)
  mo.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] })
  return () => mo.disconnect()
}

export function useTheme() {
  // The <html> class is the source of truth (set before paint by the inline script in layout.tsx).
  const dark = useSyncExternalStore(subscribeTheme,
    () => document.documentElement.classList.contains("dark"), () => true)
  const toggle = () => {
    const next = !document.documentElement.classList.contains("dark")
    const apply = () => {
      document.documentElement.classList.toggle("dark", next)
      localStorage.setItem("scout-theme", next ? "dark" : "light")
    }
    // Smooth cross-fade between themes where the browser supports it.
    if ("startViewTransition" in document) (document as Document).startViewTransition(apply)
    else apply()
  }
  return { dark, toggle }
}

export function Topbar({ onOpenPalette }: { onOpenPalette: () => void }) {
  const pathname = usePathname()
  const session = useSession()
  const { data: stats } = useStats()
  const { dark, toggle } = useTheme()
  const current = NAV.find((n) => (n.href === "/" ? pathname === "/" : pathname.startsWith(n.href)))
  const stale = hoursSince(stats?.last_ingest_at) > 6

  return (
    <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b bg-background/70 px-4 backdrop-blur-xl md:px-6">
      <AnimatePresence mode="popLayout" initial={false}>
        <motion.h2
          key={current?.label}
          initial={{ opacity: 0, y: 8, filter: "blur(4px)" }}
          animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
          exit={{ opacity: 0, y: -8, filter: "blur(4px)" }}
          className="text-sm font-medium"
        >
          {current?.label ?? "Scout"}
        </motion.h2>
      </AnimatePresence>

      <Tooltip>
        <TooltipTrigger asChild>
          <div className="hidden items-center gap-2 rounded-full border bg-card/50 px-2.5 py-1 text-xs text-muted-foreground sm:flex">
            {stats?.last_ingest_at ? <PulseDot tone={stale ? "warning" : "success"} /> : <span className="size-2 rounded-full bg-muted-foreground/40" />}
            {stats?.last_ingest_at ? `Synced ${timeAgo(stats.last_ingest_at)}` : "Not synced yet"}
          </div>
        </TooltipTrigger>
        <TooltipContent>
          {stats?.last_ingest_at
            ? `Last successful job ingestion: ${new Date(stats.last_ingest_at).toLocaleString("en-IN")}`
            : "No ingestion run has finished yet."}
        </TooltipContent>
      </Tooltip>

      <div className="ml-auto flex items-center gap-1.5">
        <button
          onClick={onOpenPalette}
          className="group flex h-8 items-center gap-2 rounded-lg border bg-card/50 px-2.5 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
        >
          <Search className="size-3.5" />
          <span className="hidden sm:inline">Search or jump to…</span>
          <Kbd className="ml-2 hidden sm:inline-flex">⌘K</Kbd>
        </button>

        <Button variant="ghost" size="icon" onClick={toggle} aria-label="Toggle theme" className="relative overflow-hidden">
          <AnimatePresence mode="wait" initial={false}>
            <motion.span
              key={dark ? "moon" : "sun"}
              initial={{ y: -18, rotate: -90, opacity: 0 }}
              animate={{ y: 0, rotate: 0, opacity: 1 }}
              exit={{ y: 18, rotate: 90, opacity: 0 }}
              transition={{ duration: 0.2 }}
            >
              {dark ? <Moon className="size-4" /> : <Sun className="size-4" />}
            </motion.span>
          </AnimatePresence>
        </Button>

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button className="grid size-8 place-items-center rounded-full bg-gradient-to-br from-brand to-info text-xs font-semibold text-brand-foreground ring-2 ring-background transition-transform hover:scale-105">
              {(session?.user.email ?? "D")[0]?.toUpperCase()}
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-56">
            <DropdownMenuLabel className="truncate font-normal text-muted-foreground">{session?.user.email ?? "Local dev mode"}</DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={() => getSupabase().auth.signOut()}>
              <LogOut /> Sign out
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  )
}
