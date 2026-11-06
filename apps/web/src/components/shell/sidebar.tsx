"use client"

import { motion } from "motion/react"
import Link from "next/link"
import { usePathname } from "next/navigation"

import { Logo } from "@/components/logo"
import { useStats } from "@/lib/queries"
import { cn } from "@/lib/utils"

import { NAV } from "./nav"

export function Sidebar() {
  const pathname = usePathname()
  const { data: stats } = useStats()

  return (
    <aside className="sticky top-0 hidden h-dvh w-60 shrink-0 flex-col border-r border-sidebar-border bg-sidebar/80 px-3 py-4 backdrop-blur-xl md:flex">
      <Link href="/" className="mb-6 flex items-center gap-2.5 px-2">
        <Logo />
        <div className="leading-tight">
          <p className="text-sm font-semibold tracking-tight">Scout</p>
          <p className="text-[11px] text-muted-foreground">Job hunter</p>
        </div>
      </Link>

      <nav className="flex flex-col gap-0.5">
        {NAV.map((item) => {
          const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href)
          const Icon = item.icon
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "group relative flex h-9 items-center gap-2.5 rounded-lg px-2.5 text-sm transition-colors",
                active ? "text-sidebar-accent-foreground" : "text-sidebar-foreground/70 hover:text-sidebar-accent-foreground",
              )}
            >
              {active && (
                <motion.span
                  layoutId="nav-active"
                  className="absolute inset-0 rounded-lg bg-sidebar-accent shadow-[inset_0_1px_0_oklch(1_0_0/6%)]"
                  transition={{ type: "spring", stiffness: 500, damping: 38 }}
                />
              )}
              {active && (
                <motion.span
                  layoutId="nav-active-bar"
                  className="absolute top-2 bottom-2 -left-3 w-[3px] rounded-r-full bg-brand"
                  transition={{ type: "spring", stiffness: 500, damping: 38 }}
                />
              )}
              <Icon className={cn("relative size-4 transition-transform group-hover:scale-110", active && "text-brand")} />
              <span className="relative flex-1">{item.label}</span>
              {item.href === "/" && stats && stats.open_in_feed > 0 && (
                <span className="relative rounded-md bg-brand-soft px-1.5 py-0.5 text-[11px] font-medium tabular-nums text-brand">
                  {stats.open_in_feed}
                </span>
              )}
              {item.phase && (
                <span className="relative rounded border px-1 text-[10px] text-muted-foreground">P{item.phase}</span>
              )}
            </Link>
          )
        })}
      </nav>

      <div className="mt-auto rounded-xl border bg-card/50 p-3 text-xs">
        <p className="font-medium">Coverage</p>
        <div className="mt-2 grid grid-cols-2 gap-2 text-muted-foreground">
          <div>
            <p className="text-base font-semibold tabular-nums text-foreground">{stats?.boards_active ?? "–"}</p>
            <p>job boards</p>
          </div>
          <div>
            <p className="text-base font-semibold tabular-nums text-foreground">{stats?.jobs_seen_total?.toLocaleString("en-IN") ?? "–"}</p>
            <p>jobs scanned</p>
          </div>
        </div>
      </div>
    </aside>
  )
}

export function MobileTabBar() {
  const pathname = usePathname()
  const items = NAV.filter((n) => ["/", "/pipeline", "/startups", "/saved-searches", "/settings"].includes(n.href))
  return (
    <nav className="fixed inset-x-0 bottom-0 z-40 border-t bg-background/80 pb-[env(safe-area-inset-bottom)] backdrop-blur-xl md:hidden">
      <div className="grid grid-cols-5">
        {items.map((item) => {
          const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href)
          const Icon = item.icon
          return (
            <Link key={item.href} href={item.href} className="relative flex flex-col items-center gap-1 py-2.5 text-[10px]">
              {active && (
                <motion.span layoutId="tab-active" className="absolute inset-x-4 top-0 h-0.5 rounded-full bg-brand" />
              )}
              <Icon className={cn("size-5", active ? "text-brand" : "text-muted-foreground")} />
              <span className={active ? "text-foreground" : "text-muted-foreground"}>{item.label.split(" ")[0]}</span>
            </Link>
          )
        })}
      </div>
    </nav>
  )
}
