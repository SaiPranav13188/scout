"use client"

import { ArrowUpRight, Building2, Check, CircleCheck, CircleX, ExternalLink, Link2, ShieldCheck, X } from "lucide-react"
import { motion } from "motion/react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { FAMILY_LABEL, LOCATION_LABEL, SOURCE_LABEL, timeAgo, TRUST_META, ycShort } from "@/lib/format"
import { useCorrectExperience, useJob } from "@/lib/queries"
import type { Job } from "@/lib/types"
import { cn } from "@/lib/utils"

import { CompanyMark, FitMeter, FresherBadge, TrustBadge } from "./badges"

function Section({ title, icon: Icon, children, delay = 0 }: {
  title: string
  icon: React.ComponentType<{ className?: string }>
  children: React.ReactNode
  delay?: number
}) {
  return (
    <motion.section initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay }} className="space-y-2.5">
      <h4 className="flex items-center gap-2 text-xs font-medium tracking-wide text-muted-foreground uppercase">
        <Icon className="size-3.5" /> {title}
      </h4>
      {children}
    </motion.section>
  )
}

export function JobSheet({ jobId, preview, onClose, onOpen, onSkip, onApplied }: {
  jobId: string | null
  preview?: Job
  onClose: () => void
  onOpen: (job: Job) => void
  onSkip: (job: Job) => void
  onApplied: (job: Job) => void
}) {
  const { data: detail, isLoading } = useJob(jobId)
  const correct = useCorrectExperience()
  const job = detail ?? preview

  return (
    <Sheet open={Boolean(jobId)} onOpenChange={(o) => !o && onClose()}>
      <SheetContent className="w-full gap-0 overflow-y-auto border-l bg-background/95 p-0 backdrop-blur-2xl sm:max-w-xl">
        {job && (
          <>
            <SheetHeader className="sticky top-0 z-10 border-b bg-background/80 p-5 backdrop-blur-xl">
              <div className="flex items-start gap-4 pr-6">
                <CompanyMark name={job.company.name} size="lg" />
                <div className="min-w-0">
                  <SheetTitle className="text-lg leading-snug">{job.title}</SheetTitle>
                  <SheetDescription className="mt-1">
                    {job.company.name}
                    {job.company.yc_batch && ` · YC ${ycShort(job.company.yc_batch)}`}
                    {" · "}{LOCATION_LABEL[job.location_bucket]}
                  </SheetDescription>
                </div>
              </div>
              <div className="mt-4 flex flex-wrap gap-2">
                <Button onClick={() => onOpen(job)} className="bg-brand text-brand-foreground hover:bg-brand/90">
                  Open job <ArrowUpRight />
                </Button>
                <Button variant="outline" onClick={() => onApplied(job)}><Check /> Mark applied</Button>
                <Button variant="ghost" onClick={() => onSkip(job)}><X /> Skip</Button>
              </div>
            </SheetHeader>

            <div className="space-y-7 p-5">
              <Section title="At a glance" icon={Building2}>
                <div className="flex flex-wrap gap-1.5">
                  <FresherBadge job={job} />
                  <TrustBadge trust={job.trust} />
                  <span className="inline-flex h-6 items-center rounded-md bg-secondary px-2 text-[11px] font-medium">{FAMILY_LABEL[job.role_family]}</span>
                </div>
                <dl className="grid grid-cols-2 gap-x-4 gap-y-3 rounded-xl border bg-card/50 p-4 text-sm">
                  {[
                    ["Location", [job.location, ...job.locations.slice(1, 4)].filter(Boolean).join(" · ") || "Not stated"],
                    ["Pay", job.salary_text ?? "Not stated"],
                    ["Type", job.employment_type ?? "Not stated"],
                    ["Team", job.department ?? "Not stated"],
                    ["Posted", job.posted_at ? timeAgo(job.posted_at) : `Seen ${timeAgo(job.first_seen_at)}`],
                    ["Source", SOURCE_LABEL[job.source] ?? job.source],
                  ].map(([k, v]) => (
                    <div key={k}>
                      <dt className="text-xs text-muted-foreground">{k}</dt>
                      <dd className="mt-0.5 truncate">{v}</dd>
                    </div>
                  ))}
                  <div className="col-span-2 flex items-center justify-between border-t pt-3">
                    <dt className="text-xs text-muted-foreground">Role fit (rules, not resume match)</dt>
                    <FitMeter score={job.relevance_score} />
                  </div>
                </dl>
              </Section>

              <Section title="Is this really entry-level?" icon={CircleCheck} delay={0.05}>
                <ul className="space-y-1.5 text-sm">
                  {(job.fresher_reasons.length ? job.fresher_reasons : ["No experience requirement found in the posting."]).map((r, i) => (
                    <li key={i} className="flex gap-2"><span className="mt-2 size-1 shrink-0 rounded-full bg-muted-foreground" />{r}</li>
                  ))}
                </ul>
                {job.experience_unclear && (
                  <div className="flex flex-col gap-3 rounded-xl border border-warning/30 bg-warning-soft p-3 text-sm sm:flex-row sm:items-center">
                    <span className="sm:flex-1">The posting doesn&apos;t say clearly. Your call:</span>
                    <div className="flex gap-2">
                    <Button size="sm" variant="outline" disabled={correct.isPending}
                      onClick={() => correct.mutate({ id: job.id, entry: true }, { onSuccess: () => toast.success("Marked as entry-level") })}>
                      <CircleCheck /> Entry-level
                    </Button>
                    <Button size="sm" variant="outline" disabled={correct.isPending}
                      onClick={() => correct.mutate({ id: job.id, entry: false }, {
                        onSuccess: () => { toast("Hidden from your feed", { description: "Marked as not entry-level." }); onClose() },
                      })}>
                      <CircleX /> Not for freshers
                    </Button>
                    </div>
                  </div>
                )}
              </Section>

              <Section title="Trust check" icon={ShieldCheck} delay={0.1}>
                <p className="text-sm text-muted-foreground">{TRUST_META[job.trust.level].hint}</p>
                <ul className="space-y-1.5">
                  {job.trust.signals.map((s, i) => (
                    <motion.li key={i} initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: 0.12 + i * 0.04 }}
                      className="flex items-center gap-2.5 rounded-lg border bg-card/40 px-3 py-2 text-sm">
                      <span className={cn("grid size-5 place-items-center rounded-full text-[11px] font-bold",
                        s.weight > 0 ? "bg-success-soft text-success" : "bg-danger-soft text-danger")}>
                        {s.weight > 0 ? "+" : "!"}
                      </span>
                      <span className="flex-1">{s.detail}</span>
                    </motion.li>
                  ))}
                  {job.trust.signals.length === 0 && <li className="text-sm text-muted-foreground">No signals either way.</li>}
                </ul>
                <div className="flex items-center gap-2 text-xs text-muted-foreground">
                  <Link2 className="size-3.5" />
                  Link check: {job.verify_status ? `${job.verify_status.replace("_", " ")}` : "not checked yet"}
                  {job.verify_reason && ` · ${job.verify_reason}`}
                  {job.verified_at && ` · ${timeAgo(job.verified_at)}`}
                </div>
              </Section>

              {job.company.description && (
                <Section title="About the company" icon={Building2} delay={0.15}>
                  <p className="text-sm leading-relaxed text-muted-foreground">{job.company.description}</p>
                  {job.company.website && (
                    <a href={job.company.website} target="_blank" rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 text-sm text-brand hover:underline">
                      {job.company.website.replace(/^https?:\/\//, "")} <ExternalLink className="size-3" />
                    </a>
                  )}
                </Section>
              )}

              <Section title="Job description" icon={Building2} delay={0.2}>
                {isLoading && !detail ? (
                  <div className="space-y-2">{Array.from({ length: 8 }).map((_, i) => (
                    <div key={i} className="skeleton-shimmer h-3.5 rounded" style={{ width: `${70 + ((i * 37) % 30)}%` }} />
                  ))}</div>
                ) : detail?.description ? (
                  <div className="text-sm leading-relaxed whitespace-pre-line text-foreground/85">{detail.description}</div>
                ) : (
                  <p className="text-sm text-muted-foreground">Details unavailable. Open the job to read the original posting.</p>
                )}
              </Section>
            </div>
          </>
        )}
      </SheetContent>
    </Sheet>
  )
}
