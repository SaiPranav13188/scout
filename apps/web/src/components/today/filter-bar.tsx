"use client"

import { Check, Clock, Globe2, Layers, MapPin, Search, X } from "lucide-react"
import { AnimatePresence, motion } from "motion/react"

import { Kbd } from "@/components/ui/kbd"
import { FAMILY_LABEL } from "@/lib/format"
import type { JobFilters, JobPage, RoleFamily } from "@/lib/types"
import { cn } from "@/lib/utils"

const FAMILIES: RoleFamily[] = ["applied_ai", "fde", "backend", "fullstack", "software"]

function Chip({ active, onClick, children, count }: {
  active: boolean
  onClick: () => void
  children: React.ReactNode
  count?: number
}) {
  return (
    <motion.button
      layout
      whileTap={{ scale: 0.95 }}
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "relative flex h-8 items-center gap-1.5 rounded-full border px-3 text-xs font-medium transition-colors",
        active
          ? "border-brand/40 bg-brand-soft text-foreground"
          : "bg-card/40 text-muted-foreground hover:border-foreground/20 hover:text-foreground",
      )}
    >
      <AnimatePresence initial={false}>
        {active && (
          <motion.span initial={{ width: 0, opacity: 0 }} animate={{ width: "auto", opacity: 1 }} exit={{ width: 0, opacity: 0 }}>
            <Check className="size-3 text-brand" />
          </motion.span>
        )}
      </AnimatePresence>
      {children}
      {count !== undefined && <span className="tabular-nums text-muted-foreground/70">{count}</span>}
    </motion.button>
  )
}

function Segmented<T extends string>({ value, options, onChange, id }: {
  value: T
  options: { value: T; label: string }[]
  onChange: (v: T) => void
  id: string
}) {
  return (
    <div className="flex h-8 items-center rounded-lg border bg-card/40 p-0.5">
      {options.map((o) => (
        <button
          key={o.value}
          onClick={() => onChange(o.value)}
          className={cn("relative h-full rounded-md px-3 text-xs font-medium transition-colors",
            value === o.value ? "text-foreground" : "text-muted-foreground hover:text-foreground")}
        >
          {value === o.value && (
            <motion.span layoutId={`seg-${id}`} className="absolute inset-0 rounded-md bg-accent shadow-sm"
              transition={{ type: "spring", stiffness: 500, damping: 35 }} />
          )}
          <span className="relative">{o.label}</span>
        </button>
      ))}
    </div>
  )
}

export function FilterBar({ filters, onChange, facets, total }: {
  filters: JobFilters
  onChange: (f: JobFilters) => void
  facets?: JobPage["facets"]
  total?: number
}) {
  const toggleIn = (key: "family" | "location", v: string) => {
    const cur = filters[key] ?? []
    onChange({ ...filters, [key]: cur.includes(v) ? cur.filter((x) => x !== v) : [...cur, v] })
  }
  const activeCount =
    (filters.family?.length ?? 0) + (filters.location?.length ?? 0) +
    (filters.posted_within_hours ? 1 : 0) + (filters.all_roles ? 1 : 0) + (filters.q ? 1 : 0)

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-[220px] flex-1">
          <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
          <input
            id="feed-search"
            value={filters.q ?? ""}
            onChange={(e) => onChange({ ...filters, q: e.target.value || undefined })}
            placeholder="Search title, company or city"
            className="h-9 w-full rounded-lg border bg-card/40 pr-10 pl-9 text-sm outline-none transition-[border,box-shadow] placeholder:text-muted-foreground focus:border-brand/50 focus:ring-3 focus:ring-brand/15"
          />
          <Kbd className="absolute top-1/2 right-2 -translate-y-1/2">/</Kbd>
        </div>
        <Segmented
          id="sort"
          value={filters.sort ?? "newest"}
          onChange={(sort) => onChange({ ...filters, sort })}
          options={[{ value: "newest", label: "Newest" }, { value: "relevance", label: "Best fit" }]}
        />
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Layers className="mr-0.5 size-3.5 text-muted-foreground" />
        {FAMILIES.map((f) => (
          <Chip key={f} active={filters.family?.includes(f) ?? false} onClick={() => toggleIn("family", f)} count={facets?.family?.[f]}>
            {FAMILY_LABEL[f]}
          </Chip>
        ))}
        <span className="mx-1 h-4 w-px bg-border" />
        <Chip active={filters.location?.includes("india") ?? false} onClick={() => toggleIn("location", "india")} count={facets?.location?.india}>
          <MapPin className="size-3" /> India
        </Chip>
        <Chip active={filters.location?.includes("remote_global") ?? false} onClick={() => toggleIn("location", "remote_global")} count={facets?.location?.remote_global}>
          <Globe2 className="size-3" /> Remote
        </Chip>
        <Chip active={filters.posted_within_hours === 24} onClick={() => onChange({ ...filters, posted_within_hours: filters.posted_within_hours ? undefined : 24 })}>
          <Clock className="size-3" /> Last 24h
        </Chip>
        <Chip active={Boolean(filters.all_roles)} onClick={() => onChange({ ...filters, all_roles: !filters.all_roles })}>
          Show all roles
        </Chip>

        <div className="ml-auto flex items-center gap-3 text-xs text-muted-foreground">
          <AnimatePresence>
            {activeCount > 0 && (
              <motion.button
                initial={{ opacity: 0, scale: 0.9 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0, scale: 0.9 }}
                onClick={() => onChange({ sort: filters.sort })}
                className="flex items-center gap-1 rounded-md px-2 py-1 hover:bg-accent hover:text-foreground"
              >
                <X className="size-3" /> Clear {activeCount}
              </motion.button>
            )}
          </AnimatePresence>
          {total !== undefined && (
            <motion.span key={total} initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }} className="tabular-nums">
              {total.toLocaleString("en-IN")} {total === 1 ? "job" : "jobs"}
            </motion.span>
          )}
        </div>
      </div>
    </div>
  )
}
