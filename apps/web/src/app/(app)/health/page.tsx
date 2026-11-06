"use client"

import { useQuery } from "@tanstack/react-query"
import { ChevronDown } from "lucide-react"
import { AnimatePresence, motion } from "motion/react"
import { useMemo, useState } from "react"

import { fadeUp, PulseDot, stagger } from "@/components/motion"
import { PageContainer, PageHeader } from "@/components/page-header"
import { RadarEmpty } from "@/components/today/empty-state"
import { api } from "@/lib/api"
import { timeAgo } from "@/lib/format"
import { keys } from "@/lib/queries"
import type { AgentRun } from "@/lib/types"
import { cn } from "@/lib/utils"

const WORKERS: Record<string, string> = {
  ingest: "Job ingestion",
  verify: "Link verification",
  discovery: "Company discovery",
  daily_digest: "Morning digest",
  evening_summary: "Evening summary",
}

const STATUS = {
  success: { label: "Healthy", dot: "success", text: "text-success" },
  partial: { label: "Partial", dot: "warning", text: "text-warning" },
  error: { label: "Failed", dot: "danger", text: "text-danger" },
  running: { label: "Running", dot: "brand", text: "text-brand" },
} as const

function duration(r: AgentRun) {
  if (!r.finished_at) return "…"
  const s = (new Date(r.finished_at).getTime() - new Date(r.started_at).getTime()) / 1000
  if (s < 60) return `${s.toFixed(1)}s`
  const total = Math.round(s)
  return `${Math.floor(total / 60)}m ${total % 60}s`
}

function RunRow({ run }: { run: AgentRun }) {
  const [open, setOpen] = useState(false)
  const st = STATUS[run.status]
  const counts = Object.entries(run.counts ?? {}).filter(([, v]) => v)
  return (
    <motion.div layout variants={fadeUp} className="rounded-xl border bg-card/50">
      <button onClick={() => setOpen((o) => !o)} className="flex w-full items-center gap-3 px-4 py-3 text-left text-sm">
        <PulseDot tone={st.dot} className={run.status === "running" ? "" : "[&>span:first-child]:hidden"} />
        <span className="w-40 shrink-0 font-medium">{WORKERS[run.worker] ?? run.worker}</span>
        <span className={cn("w-16 shrink-0 text-xs", st.text)}>{st.label}</span>
        <span className="hidden flex-1 truncate text-xs text-muted-foreground sm:block">
          {counts.slice(0, 4).map(([k, v]) => `${k.replace(/_/g, " ")} ${v}`).join(" · ")}
        </span>
        <span className="ml-auto shrink-0 text-xs tabular-nums text-muted-foreground">{timeAgo(run.started_at)} · {duration(run)}</span>
        <motion.span animate={{ rotate: open ? 180 : 0 }}><ChevronDown className="size-4 text-muted-foreground" /></motion.span>
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }} className="overflow-hidden">
            <div className="space-y-3 border-t px-4 py-3 text-xs">
              {counts.length > 0 && (
                <div className="flex flex-wrap gap-2">
                  {counts.map(([k, v]) => (
                    <span key={k} className="rounded-md bg-secondary px-2 py-1 tabular-nums"><span className="text-muted-foreground">{k.replace(/_/g, " ")}</span> {v}</span>
                  ))}
                </div>
              )}
              {run.error && <pre className="max-h-48 overflow-auto rounded-lg bg-danger-soft p-3 font-mono whitespace-pre-wrap text-danger">{run.error}</pre>}
              <p className="text-muted-foreground">Slot {run.task_key ?? "manual"} · started {new Date(run.started_at).toLocaleString("en-IN")}</p>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  )
}

export default function HealthPage() {
  const { data, isLoading } = useQuery({ queryKey: keys.runs, queryFn: api.agentRuns, refetchInterval: 20_000 })
  const latest = useMemo(() => {
    const m = new Map<string, AgentRun>()
    for (const r of data ?? []) if (!m.has(r.worker)) m.set(r.worker, r)
    return m
  }, [data])

  return (
    <PageContainer>
      <PageHeader title="Agent health" description="Every worker run is recorded. Failures also alert you on Telegram. Full metrics and LLM cost arrive in Phase 5." />
      <motion.div variants={stagger(0.05)} initial="hidden" animate="show" className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        {Object.entries(WORKERS).map(([w, label]) => {
          const r = latest.get(w)
          const st = r ? STATUS[r.status] : null
          return (
            <motion.div key={w} variants={fadeUp} className="rounded-xl border bg-card/60 p-4">
              <div className="flex items-center justify-between">
                <p className="text-xs text-muted-foreground">{label}</p>
                {st ? <PulseDot tone={st.dot} /> : <span className="size-2 rounded-full bg-muted-foreground/30" />}
              </div>
              <p className={cn("mt-2 text-sm font-medium", st?.text)}>{st?.label ?? "Never run"}</p>
              <p className="mt-0.5 text-xs text-muted-foreground">{r ? timeAgo(r.started_at) : "–"}</p>
            </motion.div>
          )
        })}
      </motion.div>
      <div>
        <h2 className="mb-3 text-sm font-medium">Recent runs</h2>
        {isLoading ? (
          <div className="space-y-2">{Array.from({ length: 5 }).map((_, i) => <div key={i} className="skeleton-shimmer h-12 rounded-xl" />)}</div>
        ) : !data?.length ? (
          <RadarEmpty title="No runs yet" body="Once the workers are running, each run shows up here with its counts and any errors." />
        ) : (
          <motion.div variants={stagger(0.02)} initial="hidden" animate="show" className="space-y-2">
            {data.map((r) => <RunRow key={r.id} run={r} />)}
          </motion.div>
        )}
      </div>
    </PageContainer>
  )
}
