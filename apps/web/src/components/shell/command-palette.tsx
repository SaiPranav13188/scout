"use client"

import { useQuery } from "@tanstack/react-query"
import { Command } from "cmdk"
import { ArrowUpRight, Briefcase, CornerDownLeft, LogOut, Moon, Search } from "lucide-react"
import { AnimatePresence, motion } from "motion/react"
import { useRouter } from "next/navigation"
import { useDeferredValue, useState } from "react"

import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog"
import { Kbd } from "@/components/ui/kbd"
import { api } from "@/lib/api"
import { FAMILY_LABEL } from "@/lib/format"
import { getSupabase } from "@/lib/supabase"

import { NAV } from "./nav"
import { useTheme } from "./topbar"

const itemCls =
  "group flex cursor-pointer items-center gap-3 rounded-lg px-3 py-2.5 text-sm text-muted-foreground outline-none transition-colors data-[selected=true]:bg-accent data-[selected=true]:text-foreground"

export function CommandPalette({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  const router = useRouter()
  const { toggle } = useTheme()
  const [search, setSearch] = useState("")
  const deferred = useDeferredValue(search.trim())

  const { data: jobs, isFetching } = useQuery({
    queryKey: ["palette-jobs", deferred],
    queryFn: () => api.jobs({ q: deferred, all_roles: true }),
    enabled: open && deferred.length >= 2,
    staleTime: 15_000,
  })

  const setOpen = (v: boolean) => {
    if (!v) setSearch("")
    onOpenChange(v)
  }

  const run = (fn: () => void) => {
    setOpen(false)
    fn()
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent showCloseButton={false} className="top-[18%] translate-y-0 overflow-hidden border-border/60 bg-popover/90 p-0 shadow-2xl backdrop-blur-2xl sm:max-w-xl">
        <DialogTitle className="sr-only">Command palette</DialogTitle>
        <Command loop shouldFilter={!deferred || deferred.length < 2} className="flex flex-col">
          <div className="flex items-center gap-3 border-b px-4">
            <Search className="size-4 text-muted-foreground" />
            <Command.Input
              value={search}
              onValueChange={setSearch}
              placeholder="Search jobs, companies, or jump to a page…"
              className="h-13 flex-1 bg-transparent text-[15px] outline-none placeholder:text-muted-foreground"
            />
            <AnimatePresence>
              {isFetching && (
                <motion.span initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
                  className="size-3.5 animate-spin rounded-full border-2 border-muted-foreground/30 border-t-brand" />
              )}
            </AnimatePresence>
          </div>
          <Command.List className="max-h-[min(60vh,420px)] overflow-y-auto p-2">
            <Command.Empty className="py-10 text-center text-sm text-muted-foreground">
              {deferred.length >= 2 ? "No matching jobs in your feed." : "Nothing found."}
            </Command.Empty>

            {jobs && jobs.items.length > 0 && (
              <Command.Group heading="Jobs" className="[&_[cmdk-group-heading]]:px-3 [&_[cmdk-group-heading]]:py-2 [&_[cmdk-group-heading]]:text-xs [&_[cmdk-group-heading]]:text-muted-foreground">
                {jobs.items.slice(0, 8).map((j) => (
                  <Command.Item key={j.id} value={`job-${j.id}`} onSelect={() => run(() => router.push(`/?job=${j.id}`))} className={itemCls}>
                    <Briefcase className="size-4 shrink-0" />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-foreground">{j.title}</p>
                      <p className="truncate text-xs">{j.company.name} · {FAMILY_LABEL[j.role_family]}</p>
                    </div>
                    <button
                      onClick={(e) => { e.stopPropagation(); window.open(j.apply_url, "_blank", "noopener,noreferrer") }}
                      className="rounded-md p-1 opacity-0 transition-opacity group-data-[selected=true]:opacity-100 hover:bg-background"
                      aria-label="Open original job page"
                    >
                      <ArrowUpRight className="size-4" />
                    </button>
                  </Command.Item>
                ))}
              </Command.Group>
            )}

            <Command.Group heading="Go to" className="[&_[cmdk-group-heading]]:px-3 [&_[cmdk-group-heading]]:py-2 [&_[cmdk-group-heading]]:text-xs [&_[cmdk-group-heading]]:text-muted-foreground">
              {NAV.map((n) => (
                <Command.Item key={n.href} value={`go ${n.label}`} onSelect={() => run(() => router.push(n.href))} className={itemCls}>
                  <n.icon className="size-4" />
                  <span className="flex-1 text-foreground">{n.label}</span>
                  <span className="flex gap-1">{n.shortcut.split(" ").map((k) => <Kbd key={k}>{k}</Kbd>)}</span>
                </Command.Item>
              ))}
            </Command.Group>

            <Command.Group heading="Actions" className="[&_[cmdk-group-heading]]:px-3 [&_[cmdk-group-heading]]:py-2 [&_[cmdk-group-heading]]:text-xs [&_[cmdk-group-heading]]:text-muted-foreground">
              <Command.Item value="toggle theme" onSelect={() => run(toggle)} className={itemCls}>
                <Moon className="size-4" /> <span className="text-foreground">Toggle light / dark</span>
              </Command.Item>
              <Command.Item value="sign out" onSelect={() => run(() => getSupabase().auth.signOut())} className={itemCls}>
                <LogOut className="size-4" /> <span className="text-foreground">Sign out</span>
              </Command.Item>
            </Command.Group>
          </Command.List>
          <div className="flex items-center justify-between border-t px-4 py-2 text-[11px] text-muted-foreground">
            <span className="flex items-center gap-1.5"><Kbd>↑</Kbd><Kbd>↓</Kbd> navigate</span>
            <span className="flex items-center gap-1.5"><Kbd><CornerDownLeft className="size-3" /></Kbd> select</span>
            <span className="flex items-center gap-1.5"><Kbd>esc</Kbd> close</span>
          </div>
        </Command>
      </DialogContent>
    </Dialog>
  )
}
