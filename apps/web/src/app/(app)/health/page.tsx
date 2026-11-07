"use client"

import { useQuery } from "@tanstack/react-query"
import { ChevronDown } from "lucide-react"
import { AnimatePresence, motion } from "motion/react"
import { useState } from "react"

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
  gmail: "Gmail job alerts",
  scoring: "Scoring + resumes",
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
      <button onClick={() => setOpen((o) => !o)} className="flex w-full min-w-0 items-center gap-2 px-3 py-3 text-left text-sm sm:gap-3 sm:px-4">
        <PulseDot tone={st.dot} className={run.status === "running" ? "" : "[&>span:first-child]:hidden"} />
        <span className="min-w-0 flex-1 truncate font-medium sm:w-40 sm:flex-none">{WORKERS[run.worker] ?? run.worker}</span>
        <span className={cn("w-16 shrink-0 text-xs", st.text)}>{st.label}</span>
        <span className="hidden flex-1 truncate text-xs text-muted-foreground sm:block">
          {counts.slice(0, 4).map(([k, v]) => `${k.replace(/_/g, " ")} ${v}`).join(" · ")}
        </span>
        <span className="ml-auto shrink-0 text-xs tabular-nums text-muted-foreground">{timeAgo(run.started_at)}<span className="hidden sm:inline"> · {duration(run)}</span></span>
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

function inTime(iso: string | null) {
  if (!iso) return "–"
  const mins = Math.round((new Date(iso).getTime() - Date.now()) / 60_000)
  if (mins <= 1) return "on the next tick"
  if (mins < 60) return `in ${mins} min`
  if (mins < 48 * 60) return `in ${Math.round(mins / 60)} h`
  return `in ${Math.round(mins / 1440)} days`
}

export default function HealthPage() {
  const { data, isLoading } = useQuery({ queryKey: keys.runs, queryFn: api.agentRuns, refetchInterval: 20_000 })
  const { data: summary } = useQuery({ queryKey: ["health-summary"], queryFn: api.healthSummary, refetchInterval: 30_000 })

  return (
    <PageContainer>
      <PageHeader title="Agent health"
        description="Every worker run is recorded. Failures also alert you on Telegram (at most one per worker per hour)." />
      {summary?.paused && (
        <p className="rounded-2xl border border-warning/30 bg-warning-soft p-3.5 text-sm">Paused: nothing runs until you resume (Settings or /resume on Telegram).</p>
      )}
      <motion.div variants={stagger(0.04)} initial="hidden" animate="show" className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {Object.entries(WORKERS).map(([w, label]) => {
          const h = summary?.workers.find((x) => x.worker === w)
          const st = h?.last_status ? STATUS[h.last_status as keyof typeof STATUS] : null
          return (
            <motion.div key={w} variants={fadeUp} className="space-y-2 rounded-xl border bg-card/60 p-4">
              <div className="flex items-center justify-between">
                <p className="text-xs text-muted-foreground">{label}</p>
                {st ? <PulseDot tone={st.dot} /> : <span className="size-2 rounded-full bg-muted-foreground/30" />}
              </div>
              <p className={cn("text-sm font-medium", st?.text)}>{st?.label ?? (summary ? "Never run" : "…")}</p>
              <dl className="space-y-0.5 text-xs text-muted-foreground">
                <div className="flex justify-between gap-2"><dt>Last success</dt><dd>{h?.last_success_at ? timeAgo(h.last_success_at) : "–"}</dd></div>
                <div className="flex justify-between gap-2"><dt>Next run</dt><dd>{h ? inTime(h.next_due_at) : "–"}</dd></div>
                <div className="flex justify-between gap-2"><dt>Last 24 h</dt>
                  <dd className="tabular-nums">{h ? `${h.runs_24h} runs · ${h.failures_24h} failed${h.retries_24h ? ` · ${h.retries_24h} retries` : ""}` : "–"}</dd></div>
              </dl>
              {h?.last_error && <p className="line-clamp-2 text-xs text-danger" title={h.last_error}>{h.last_error}</p>}
            </motion.div>
          )
        })}
        {summary && (
          <motion.div variants={fadeUp} className="space-y-2 rounded-xl border bg-card/60 p-4">
            <p className="text-xs text-muted-foreground">Gemini usage, last 24 h</p>
            <p className="text-sm font-medium tabular-nums">{summary.llm_24h.llm_calls} calls</p>
            <p className="text-xs text-muted-foreground tabular-nums">
              {summary.llm_24h.llm_input_tokens.toLocaleString("en-IN")} tokens in · {summary.llm_24h.llm_output_tokens.toLocaleString("en-IN")} out
            </p>
            <p className="text-xs text-muted-foreground">{summary.llm_cost_note}</p>
          </motion.div>
        )}
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
