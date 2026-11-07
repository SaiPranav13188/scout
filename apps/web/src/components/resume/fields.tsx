"use client"

import { ArrowDown, ArrowUp, Plus, Trash2, TriangleAlert, X } from "lucide-react"
import { useId, useState } from "react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { cn } from "@/lib/utils"

/** Which parts of the resume the fact check couldn't find in the uploaded file. */
export interface Flags {
  skills: Set<string>
  tech: Map<string, Set<string>>
  textPrefixes: string[]
}

export function readFlags(unsupported: string[]): Flags {
  const flags: Flags = { skills: new Set(), tech: new Map(), textPrefixes: [] }
  for (const u of unsupported) {
    let m = u.match(/^skill: ([\s\S]*)$/)
    if (m) { flags.skills.add(m[1].toLowerCase()); continue }
    m = u.match(/^tech in ([\s\S]*?): ([\s\S]*)$/)
    if (m) {
      const set = flags.tech.get(m[1]) ?? new Set<string>()
      set.add(m[2].toLowerCase())
      flags.tech.set(m[1], set)
      continue
    }
    m = u.match(/^number '.*?' in: ([\s\S]*)$/)
    if (m) flags.textPrefixes.push(m[1])
  }
  return flags
}

export function textFlagged(flags: Flags, text: string | null | undefined): boolean {
  if (!text) return false
  const t = text.trim()
  return flags.textPrefixes.some((p) => t.slice(0, 60) === p)
}

/** Plain-language version of a fact-check line. */
export function describeFlag(u: string): string {
  let m = u.match(/^skill: ([\s\S]*)$/)
  if (m) return `Skill "${m[1]}"`
  m = u.match(/^tech in ([\s\S]*?): ([\s\S]*)$/)
  if (m) return `"${m[2]}" in ${m[1]}`
  m = u.match(/^number '(.*?)' in: ([\s\S]*)$/)
  if (m) return `${m[1]} in "${m[2]}${m[2].length >= 60 ? "…" : ""}"`
  return u
}

export function Field({ label, className, children }: { label: string; className?: string; children: React.ReactNode }) {
  return (
    <label className={cn("grid content-start gap-1.5", className)}>
      <span className="text-xs font-medium text-muted-foreground">{label}</span>
      {children}
    </label>
  )
}

export function TextField({ label, value, onChange, placeholder, flagged, className }: {
  label: string
  value: string | null
  onChange: (v: string | null) => void
  placeholder?: string
  flagged?: boolean
  className?: string
}) {
  return (
    <Field label={label} className={className}>
      <Input value={value ?? ""} placeholder={placeholder} aria-invalid={flagged || undefined}
        className={cn(flagged && "border-warning ring-3 ring-warning/20 aria-invalid:border-warning aria-invalid:ring-warning/20")}
        onChange={(e) => onChange(e.target.value === "" ? null : e.target.value)} />
    </Field>
  )
}

/** Tags you add with Enter or a comma and remove with ×. Flagged items are tinted. */
export function ChipList({ items, onChange, placeholder, isFlagged, label }: {
  items: string[]
  onChange: (items: string[]) => void
  placeholder: string
  isFlagged?: (item: string) => boolean
  label: string
}) {
  const [text, setText] = useState("")
  const id = useId()
  const add = (raw: string) => {
    const parts = raw.split(",").map((s) => s.trim()).filter(Boolean)
    const have = new Set(items.map((i) => i.toLowerCase()))
    const next = [...items]
    for (const p of parts) if (!have.has(p.toLowerCase())) { next.push(p); have.add(p.toLowerCase()) }
    if (next.length !== items.length) onChange(next)
    setText("")
  }
  return (
    <div className="flex flex-wrap items-center gap-1.5 rounded-lg border border-input bg-transparent p-1.5 focus-within:border-ring focus-within:ring-3 focus-within:ring-ring/50 dark:bg-input/30">
      {items.map((item, i) => {
        const flagged = isFlagged?.(item)
        return (
          <span key={`${item}-${i}`} title={flagged ? "Not found in your uploaded file" : undefined}
            className={cn("inline-flex h-6 items-center gap-1 rounded-md pr-1 pl-2 text-xs font-medium",
              flagged ? "bg-warning-soft text-warning" : "bg-secondary text-secondary-foreground")}>
            {flagged && <TriangleAlert className="size-3" />}
            {item}
            <button type="button" aria-label={`Remove ${item}`} onClick={() => onChange(items.filter((_, j) => j !== i))}
              className="grid size-4 place-items-center rounded opacity-60 hover:bg-foreground/10 hover:opacity-100">
              <X className="size-3" />
            </button>
          </span>
        )
      })}
      <label htmlFor={id} className="sr-only">{label}</label>
      <input id={id} value={text} placeholder={items.length ? "Add…" : placeholder}
        className="h-6 min-w-24 flex-1 bg-transparent px-1 text-sm outline-none placeholder:text-muted-foreground"
        onChange={(e) => (e.target.value.includes(",") ? add(e.target.value) : setText(e.target.value))}
        onKeyDown={(e) => {
          if (e.key === "Enter") { e.preventDefault(); add(text) }
          else if (e.key === "Backspace" && !text && items.length) onChange(items.slice(0, -1))
        }}
        onBlur={() => text.trim() && add(text)} />
    </div>
  )
}

/** Move / remove buttons for one row of a list. */
export function RowActions({ index, count, onMove, onRemove, what }: {
  index: number
  count: number
  onMove: (from: number, to: number) => void
  onRemove: (index: number) => void
  what: string
}) {
  return (
    <div className="flex shrink-0 items-center gap-0.5">
      <Button type="button" variant="ghost" size="icon-xs" aria-label={`Move ${what} up`} disabled={index === 0}
        onClick={() => onMove(index, index - 1)}><ArrowUp /></Button>
      <Button type="button" variant="ghost" size="icon-xs" aria-label={`Move ${what} down`} disabled={index === count - 1}
        onClick={() => onMove(index, index + 1)}><ArrowDown /></Button>
      <Button type="button" variant="ghost" size="icon-xs" aria-label={`Remove ${what}`}
        className="hover:text-destructive" onClick={() => onRemove(index)}><Trash2 /></Button>
    </div>
  )
}

export function move<T>(list: T[], from: number, to: number): T[] {
  if (to < 0 || to >= list.length) return list
  const next = [...list]
  const [item] = next.splice(from, 1)
  next.splice(to, 0, item)
  return next
}

/** Editable list of sentences (bullets, achievements, certifications). */
export function LineList({ items, onChange, addLabel, flags, what, multiline = true }: {
  items: string[]
  onChange: (items: string[]) => void
  addLabel: string
  flags: Flags
  what: string
  multiline?: boolean
}) {
  return (
    <div className="space-y-2">
      {items.map((line, i) => {
        const flagged = textFlagged(flags, line)
        const set = (v: string) => onChange(items.map((x, j) => (j === i ? v : x)))
        const cls = cn("min-h-0", flagged && "border-warning ring-3 ring-warning/20")
        return (
          <div key={i} className="flex items-start gap-2">
            <span className="mt-2.5 size-1.5 shrink-0 rounded-full bg-muted-foreground/50" />
            {multiline
              ? <Textarea value={line} rows={1} aria-label={`${what} ${i + 1}`} className={cls} onChange={(e) => set(e.target.value)} />
              : <Input value={line} aria-label={`${what} ${i + 1}`} className={cls} onChange={(e) => set(e.target.value)} />}
            <RowActions index={i} count={items.length} what={what} onMove={(a, b) => onChange(move(items, a, b))}
              onRemove={(j) => onChange(items.filter((_, k) => k !== j))} />
          </div>
        )
      })}
      <Button type="button" variant="ghost" size="sm" className="text-muted-foreground" onClick={() => onChange([...items, ""])}>
        <Plus /> {addLabel}
      </Button>
    </div>
  )
}

export function SectionCard({ title, description, count, action, children }: {
  title: string
  description?: string
  count?: number
  action?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <section className="rounded-2xl border bg-card/60">
      <div className="flex items-start justify-between gap-3 border-b px-5 py-4">
        <div>
          <h2 className="flex items-center gap-2 font-medium">
            {title}
            {count !== undefined && <span className="rounded-md bg-secondary px-1.5 text-xs text-muted-foreground tabular-nums">{count}</span>}
          </h2>
          {description && <p className="text-sm text-muted-foreground">{description}</p>}
        </div>
        {action}
      </div>
      <div className="space-y-4 p-5">{children}</div>
    </section>
  )
}

export function ItemCard({ title, children, actions }: { title: string; children: React.ReactNode; actions: React.ReactNode }) {
  return (
    <div className="rounded-xl border bg-background/40">
      <div className="flex items-center justify-between gap-3 border-b px-4 py-2">
        <p className="truncate text-sm font-medium">{title}</p>
        {actions}
      </div>
      <div className="space-y-3 p-4">{children}</div>
    </div>
  )
}
