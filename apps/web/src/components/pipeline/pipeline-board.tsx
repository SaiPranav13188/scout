"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { ArrowUpRight, BellRing, Briefcase, CalendarClock, Download, PartyPopper, X } from "lucide-react"
import { motion } from "motion/react"
import { useState } from "react"
import { toast } from "sonner"

import { PageContainer, PageHeader } from "@/components/page-header"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { api, ApiError, downloadFile } from "@/lib/api"
import { timeAgo } from "@/lib/format"
import type { ApplicationRow, PipelineStatus } from "@/lib/types"
import { cn } from "@/lib/utils"

const COLUMNS: { status: PipelineStatus; label: string; tone: string }[] = [
  { status: "applied", label: "Applied", tone: "bg-brand" },
  { status: "interview", label: "Interview", tone: "bg-warning" },
  { status: "offer", label: "Offer", tone: "bg-success" },
  { status: "rejected", label: "Rejected", tone: "bg-muted-foreground" },
]
const KEY = ["applications"] as const

function toDateInput(iso: string | null) {
  if (!iso) return ""
  const d = new Date(iso)
  return new Date(d.getTime() - d.getTimezoneOffset() * 60_000).toISOString().slice(0, 10)
}

function useUpdate() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: Parameters<typeof api.updateApplication>[1] }) => api.updateApplication(id, body),
    onMutate: async ({ id, body }) => {
      await qc.cancelQueries({ queryKey: KEY })
      const prev = qc.getQueryData<ApplicationRow[]>(KEY)
      qc.setQueryData<ApplicationRow[]>(KEY, (rows) => rows?.map((r) => r.job_id === id ? {
        ...r, ...(body.status ? { status: body.status as PipelineStatus } : {}),
        ...(body.notes !== undefined ? { notes: body.notes } : {}),
        ...(body.clear_follow_up ? { follow_up_at: null, follow_up_due: false } : {}),
      } : r))
      return { prev }
    },
    onError: (e: Error, _v, ctx) => {
      qc.setQueryData(KEY, ctx?.prev)
      toast.error("Not saved", { description: e instanceof ApiError ? e.message : "Server not reachable." })
    },
    onSuccess: (row) => {
      if ("title" in row) qc.setQueryData<ApplicationRow[]>(KEY, (rows) => rows?.map((r) => (r.job_id === row.job_id ? row : r)))
      else qc.invalidateQueries({ queryKey: KEY })
      qc.invalidateQueries({ queryKey: ["stats"] })
    },
  })
}

function Card({ row }: { row: ApplicationRow }) {
  const update = useUpdate()
  const [notes, setNotes] = useState(row.notes ?? "")
  const set = (body: Parameters<typeof api.updateApplication>[1]) => update.mutate({ id: row.job_id, body })
  return (
    <motion.li layout initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}
      className={cn("space-y-3 rounded-xl border bg-card/70 p-3.5", row.follow_up_due && "border-warning/50")}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium" title={row.title}>{row.title}</p>
          <p className="truncate text-xs text-muted-foreground">{row.company}{row.location ? ` · ${row.location}` : ""}</p>
        </div>
        <a href={row.apply_url} target="_blank" rel="noopener noreferrer" aria-label={`Open ${row.title}`}
          className="grid size-7 shrink-0 place-items-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground">
          <ArrowUpRight className="size-4" />
        </a>
      </div>
      <p className="text-xs text-muted-foreground">Applied {row.applied_at ? timeAgo(row.applied_at) : "(date not recorded)"}</p>
      {row.follow_up_due && (
        <p className="flex items-center gap-1.5 rounded-md bg-warning-soft px-2 py-1 text-xs text-warning">
          <BellRing className="size-3.5" /> Follow up now
        </p>
      )}
      <Textarea value={notes} rows={2} placeholder="Notes: who you spoke to, next step…" aria-label={`Notes for ${row.title}`}
        className="min-h-0 text-xs" onChange={(e) => setNotes(e.target.value)}
        onBlur={() => notes !== (row.notes ?? "") && set({ notes })} />
      <div className="flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <CalendarClock className="size-3.5" />
          <span className="sr-only">Follow-up date</span>
          <input type="date" value={toDateInput(row.follow_up_at)} aria-label={`Follow-up date for ${row.title}`}
            className="h-7 rounded-md border border-input bg-transparent px-1.5 text-xs dark:bg-input/30"
            onChange={(e) => e.target.value
              ? set({ follow_up_at: new Date(`${e.target.value}T09:00:00`).toISOString() })
              : set({ clear_follow_up: true })} />
        </label>
        {row.follow_up_at && (
          <Button size="icon-xs" variant="ghost" aria-label="Clear follow-up" onClick={() => set({ clear_follow_up: true })}><X /></Button>
        )}
        <select value={row.status} aria-label={`Status of ${row.title}`}
          className="ml-auto h-7 rounded-md border border-input bg-transparent px-1.5 text-xs dark:bg-input/30"
          onChange={(e) => {
            set({ status: e.target.value })
            if (e.target.value === "offer") toast.success("Congratulations! 🎉")
          }}>
          {COLUMNS.map((c) => <option key={c.status} value={c.status}>{c.label}</option>)}
          <option value="discovered">Undo (back to feed)</option>
        </select>
      </div>
      {row.resume_id && (
        <Button size="xs" variant="outline" onClick={() => downloadFile(`/api/resumes/${row.resume_id}/file?format=pdf`, "resume.pdf")
          .catch((e: Error) => toast.error("Download failed", { description: e.message }))}>
          <Download /> Resume you used
        </Button>
      )}
    </motion.li>
  )
}

export function PipelineBoard() {
  const { data, isLoading, error } = useQuery({ queryKey: KEY, queryFn: api.applications })
  const due = data?.filter((r) => r.follow_up_due).length ?? 0
  return (
    <PageContainer>
      <PageHeader title="Pipeline"
        description="Jobs you marked applied. Move them along, keep notes, and get a reminder to follow up (7 days after applying, editable)." />
      {due > 0 && (
        <div className="flex items-center gap-2 rounded-2xl border border-warning/30 bg-warning-soft p-3.5 text-sm">
          <BellRing className="size-4 text-warning" /> {due} follow-up{due > 1 ? "s are" : " is"} due. They&apos;re also in your evening Telegram summary.
        </div>
      )}
      {error ? (
        <p className="text-sm text-destructive">Couldn&apos;t load your pipeline: {(error as Error).message}</p>
      ) : isLoading ? (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">{COLUMNS.map((c) => <div key={c.status} className="skeleton-shimmer h-48 rounded-2xl" />)}</div>
      ) : !data?.length ? (
        <div className="grid place-items-center rounded-2xl border border-dashed bg-card/40 px-6 py-16 text-center">
          <div className="grid size-12 place-items-center rounded-xl bg-brand-soft text-brand"><Briefcase className="size-5" /></div>
          <h2 className="mt-4 font-medium">Nothing here yet</h2>
          <p className="mt-1 max-w-sm text-sm text-muted-foreground">
            When you apply to a job, press &quot;Mark applied&quot; on it (dashboard or Telegram). It shows up here with a follow-up reminder.
          </p>
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          {COLUMNS.map((c) => {
            const rows = data.filter((r) => r.status === c.status)
            return (
              <section key={c.status} aria-label={c.label} className="min-w-0 rounded-2xl border bg-card/40 p-3">
                <h2 className="mb-3 flex items-center gap-2 px-1 text-sm font-medium">
                  <span className={cn("size-2 rounded-full", c.tone)} />{c.label}
                  <span className="rounded-md bg-secondary px-1.5 text-xs text-muted-foreground tabular-nums">{rows.length}</span>
                  {c.status === "offer" && rows.length > 0 && <PartyPopper className="size-4 text-success" />}
                </h2>
                {rows.length ? <ul className="space-y-3">{rows.map((r) => <Card key={r.job_id} row={r} />)}</ul>
                  : <p className="px-1 pb-2 text-xs text-muted-foreground">None</p>}
              </section>
            )
          })}
        </div>
      )}
    </PageContainer>
  )
}
