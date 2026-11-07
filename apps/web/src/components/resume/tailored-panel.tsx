"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Copy, Download, FileText, Loader2, MessageSquareText, RefreshCw, ShieldCheck, Sparkles, TriangleAlert } from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { api, ApiError, downloadFile } from "@/lib/api"
import { timeAgo } from "@/lib/format"
import type { TailoredResume } from "@/lib/types"
import { cn } from "@/lib/utils"

export const tailoredKey = (jobId: string) => ["tailored", jobId] as const

export function DownloadButtons({ r, size = "sm" }: { r: TailoredResume; size?: "sm" | "xs" }) {
  const get = (format: "pdf" | "docx") =>
    downloadFile(`/api/resumes/${r.id}/file?format=${format}`, `resume.${format}`)
      .catch((e: Error) => toast.error("Download failed", { description: e.message }))
  return (
    <div className="flex gap-1.5">
      <Button size={size} className="bg-brand text-brand-foreground hover:bg-brand/90" onClick={() => get("pdf")}><Download /> PDF</Button>
      <Button size={size} variant="outline" onClick={() => get("docx")}>DOCX</Button>
    </div>
  )
}

export function ResumeStatus({ r }: { r: TailoredResume }) {
  return r.tailored && r.passed ? (
    <p className="flex items-center gap-1.5 text-xs text-success"><ShieldCheck className="size-3.5" />
      Tailored · every claim checked against your resume · {r.pages} page</p>
  ) : (
    <p className="flex items-center gap-1.5 text-xs text-warning"><TriangleAlert className="size-3.5" />
      Your untailored resume (the tailored draft didn&apos;t pass the fact check)</p>
  )
}

/** Job sheet section: make, check and download this job's tailored resume. */
export function TailoredPanel({ jobId }: { jobId: string }) {
  const qc = useQueryClient()
  const { data: versions, isLoading } = useQuery({ queryKey: tailoredKey(jobId), queryFn: () => api.tailoredForJob(jobId) })
  const make = useMutation({
    mutationFn: () => api.tailorForJob(jobId),
    onSuccess: (r) => {
      qc.setQueryData<TailoredResume[]>(tailoredKey(jobId), (old) => [r, ...(old ?? [])])
      qc.invalidateQueries({ queryKey: ["tailored-all"] })
      if (r.tailored) toast.success("Tailored resume ready", { description: "Every claim was checked against your master resume." })
      else toast("Used your untailored resume", { description: "Gemini's draft made claims your resume doesn't support." })
    },
    onError: (e: Error) => toast.error("Couldn't make the resume", { description: e instanceof ApiError ? e.message : "Server not reachable." }),
  })
  const latest = versions?.[0]

  if (isLoading) return <div className="skeleton-shimmer h-20 rounded-xl" />
  return (
    <div className="space-y-3 rounded-xl border bg-card/50 p-4">
      {make.isPending ? (
        <p className="flex items-center gap-2 text-sm"><Loader2 className="size-4 animate-spin text-brand" />
          Tailoring with Gemini, then checking every claim (about 20 seconds)…</p>
      ) : latest ? (
        <>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="min-w-0">
              <p className="text-sm font-medium">Made {timeAgo(latest.created_at)} from resume v{latest.master_version}</p>
              <ResumeStatus r={latest} />
            </div>
            <DownloadButtons r={latest} />
          </div>
          {latest.emphasis.length > 0 && (
            <ul className="space-y-1 text-xs text-muted-foreground">
              {latest.emphasis.map((e, i) => <li key={i} className="flex gap-1.5"><Sparkles className="mt-0.5 size-3 shrink-0 text-brand" />{e}</li>)}
            </ul>
          )}
          {latest.dropped_to_fit.length > 0 && (
            <p className="text-xs text-muted-foreground">Left out to fit one page: {latest.dropped_to_fit.length} line{latest.dropped_to_fit.length > 1 ? "s" : ""}.</p>
          )}
          <Button size="xs" variant="ghost" className="text-muted-foreground" onClick={() => make.mutate()}>
            <RefreshCw /> Make a new version
          </Button>
        </>
      ) : (
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-sm text-muted-foreground">A one-page resume for this job, using only facts from your master resume.</p>
          <Button size="sm" className={cn("bg-brand text-brand-foreground hover:bg-brand/90")} onClick={() => make.mutate()}>
            <FileText /> Make tailored resume
          </Button>
        </div>
      )}
    </div>
  )
}


/** Job sheet: cover note + recruiter message, fact-checked like the resume. Drafts only. */
export function DraftsPanel({ jobId }: { jobId: string }) {
  const make = useMutation({
    mutationFn: () => api.drafts(jobId),
    onError: (e: Error) => toast.error("Couldn't draft", { description: e instanceof ApiError ? e.message : "Server not reachable." }),
  })
  const d = make.data
  const copy = (text: string) => navigator.clipboard.writeText(text).then(() => toast.success("Copied"))
  return (
    <div className="space-y-3 rounded-xl border bg-card/50 p-4">
      {make.isPending ? (
        <p className="flex items-center gap-2 text-sm"><Loader2 className="size-4 animate-spin text-brand" /> Drafting and fact-checking…</p>
      ) : d?.ok ? (
        <>
          {([["Cover note", d.cover_note], ["Message to a recruiter", d.recruiter_message]] as const).map(([label, text]) => (
            <div key={label} className="space-y-1.5">
              <div className="flex items-center justify-between">
                <p className="text-xs font-medium text-muted-foreground">{label}</p>
                <Button size="xs" variant="ghost" onClick={() => copy(text ?? "")}><Copy /> Copy</Button>
              </div>
              <p className="rounded-lg bg-background/60 p-3 text-sm leading-relaxed whitespace-pre-wrap">{text}</p>
            </div>
          ))}
          <p className="flex items-center gap-1.5 text-xs text-success"><ShieldCheck className="size-3.5" /> Only facts from your resume. Edit before you send; Scout never sends anything.</p>
          <Button size="xs" variant="ghost" className="text-muted-foreground" onClick={() => make.mutate()}><RefreshCw /> Draft again</Button>
        </>
      ) : (
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-sm text-muted-foreground">
            {d && !d.ok ? "Gemini's drafts made claims your resume doesn't support, so they're not shown. Try again." : "A short cover note and a message for a recruiter, from your resume's facts only."}
          </p>
          <Button size="sm" variant="outline" onClick={() => make.mutate()}><MessageSquareText /> Draft messages</Button>
        </div>
      )}
    </div>
  )
}
