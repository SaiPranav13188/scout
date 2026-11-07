"use client"

import { Loader2 } from "lucide-react"
import { AnimatePresence, motion } from "motion/react"
import { usePathname, useRouter, useSearchParams } from "next/navigation"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { toast } from "sonner"

import { isTyping } from "@/components/shell/app-shell"
import { Button } from "@/components/ui/button"
import { api } from "@/lib/api"
import { useJobs, useSetStatus } from "@/lib/queries"
import type { Job, JobFilters, JobStatus } from "@/lib/types"

import { CardSkeleton, RadarEmpty } from "./empty-state"
import { FilterBar } from "./filter-bar"
import { JobCard } from "./job-card"
import { JobSheet } from "./job-sheet"
import { LabelProgress } from "./label-progress"
import { StatsStrip } from "./stats-strip"

function greeting() {
  const h = new Date().getHours()
  return h < 5 ? "Burning the midnight oil" : h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening"
}

export function TodayView() {
  const router = useRouter()
  const pathname = usePathname()
  const params = useSearchParams()
  const openJobId = params.get("job")

  const [filters, setFilters] = useState<JobFilters>({ sort: "newest" })
  const [debouncedQ, setDebouncedQ] = useState<string | undefined>()
  useEffect(() => {
    const t = setTimeout(() => setDebouncedQ(filters.q), 250)
    return () => clearTimeout(t)
  }, [filters.q])
  const effective = useMemo(() => ({ ...filters, q: debouncedQ }), [filters, debouncedQ])

  const { data, isLoading, isError, error, fetchNextPage, hasNextPage, isFetchingNextPage, isFetching } = useJobs(effective)
  const jobs = useMemo(() => data?.pages.flatMap((p) => p.items) ?? [], [data])
  const first = data?.pages[0]

  // "New since last visit": remember the previous visit for this session, then record this one.
  const [prevVisit, setPrevVisit] = useState<string | null | undefined>(undefined)
  useEffect(() => {
    api.visit().then((r) => setPrevVisit(r.previous_visit_at)).catch(() => setPrevVisit(null))
  }, [])
  const isNew = (j: Job) => prevVisit !== undefined && (prevVisit === null || j.first_seen_at > prevVisit)

  const [activeRaw, setActive] = useState(0)
  const cardRefs = useRef<(HTMLElement | null)[]>([])
  // Clamp when cards leave the list (skip/applied) instead of syncing state in an effect.
  const active = Math.max(0, Math.min(activeRaw, jobs.length - 1))
  const setStatus = useSetStatus()

  const openDetails = useCallback((id: string | null) => {
    const sp = new URLSearchParams(params.toString())
    if (id) sp.set("job", id)
    else sp.delete("job")
    router.replace(`${pathname}${sp.size ? `?${sp}` : ""}`, { scroll: false })
  }, [params, pathname, router])

  const changeStatus = useCallback((job: Job, status: JobStatus) => {
    setStatus.mutate({ id: job.id, status }, {
      onError: (e) => toast.error("Couldn't update the job", { description: e.message }),
    })
    if (openJobId === job.id) openDetails(null)
    const label = status === "applied" ? "Marked as applied" : "Skipped"
    toast(label, {
      description: `${job.title} · ${job.company.name}`,
      action: { label: "Undo", onClick: () => setStatus.mutate({ id: job.id, status: "discovered" }) },
      duration: 6000,
    })
  }, [setStatus, openJobId, openDetails])

  // Shown after a job page opens: a nudge to mark it applied.
  const afterOpen = useCallback((job: Job) => {
    toast("Opened the original posting", {
      description: "Applied there? Mark it so it leaves your feed.",
      action: { label: "Mark applied", onClick: () => changeStatus(job, "applied") },
      duration: 8000,
    })
  }, [changeStatus])

  // Keyboard "o" and mobile swipe have no link to click, so they open the page from script.
  const openJob = useCallback((job: Job) => {
    window.open(job.apply_url, "_blank", "noopener,noreferrer")
    afterOpen(job)
  }, [afterOpen])

  // Keyboard: j/k move, o open, a applied, s skip, enter details.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isTyping(e) || e.metaKey || e.ctrlKey || e.altKey || openJobId) return
      const job = jobs[active]
      const move = (d: number) => {
        e.preventDefault()
        const next = Math.max(0, Math.min(jobs.length - 1, active + d))
        setActive(next)
        cardRefs.current[next]?.scrollIntoView({ block: "nearest", behavior: "smooth" })
      }
      if (e.key === "j" || e.key === "ArrowDown") move(1)
      else if (e.key === "k" || e.key === "ArrowUp") move(-1)
      else if (!job) return
      else if (e.key === "o") openJob(job)
      else if (e.key === "a") changeStatus(job, "applied")
      else if (e.key === "s") changeStatus(job, "skipped")
      else if (e.key === "Enter") openDetails(job.id)
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [jobs, active, openJobId, openJob, changeStatus, openDetails])

  // Infinite scroll.
  const sentinel = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = sentinel.current
    if (!el) return
    const io = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting && hasNextPage && !isFetchingNextPage) fetchNextPage()
    }, { rootMargin: "600px" })
    io.observe(el)
    return () => io.disconnect()
  }, [hasNextPage, isFetchingNextPage, fetchNextPage])

  const newCount = jobs.filter(isNew).length

  return (
    <div className="mx-auto w-full max-w-5xl space-y-6 px-4 py-6 md:px-6 md:py-8">
      <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}>
        <h1 className="text-gradient text-2xl font-semibold tracking-tight md:text-3xl">{greeting()}</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          {isLoading ? "Looking for new roles…" : newCount > 0
            ? `${newCount} new ${newCount === 1 ? "role" : "roles"} since your last visit. Newest first.`
            : "You're caught up. New roles appear here as soon as they're found."}
        </p>
        {!filters.include_unclear && (first?.facets.experience?.unclear ?? 0) > 0 && (
          <p className="mt-1 text-xs text-muted-foreground">
            Showing confirmed fresher / entry-level jobs only.{" "}
            <button className="text-brand hover:underline" onClick={() => setFilters({ ...filters, include_unclear: true })}>
              {first?.facets.experience?.unclear} more have unclear experience
            </button>{" "}
            (their job alert didn&apos;t say how much experience is needed).
          </p>
        )}
      </motion.div>

      <LabelProgress />

      <StatsStrip />

      <div className="sticky top-14 z-20 -mx-4 bg-background/80 px-4 py-3 backdrop-blur-xl md:-mx-6 md:px-6">
        <FilterBar filters={filters} onChange={setFilters} facets={first?.facets} total={first?.total} />
        <AnimatePresence>
          {isFetching && !isLoading && !isFetchingNextPage && (
            <motion.div initial={{ scaleX: 0, opacity: 1 }} animate={{ scaleX: 1 }} exit={{ opacity: 0 }}
              transition={{ duration: 0.8, ease: "easeOut" }}
              className="absolute inset-x-0 bottom-0 h-px origin-left bg-gradient-to-r from-transparent via-brand to-transparent" />
          )}
        </AnimatePresence>
      </div>

      {isError ? (
        <RadarEmpty title="Couldn't load your jobs" body={(error as Error).message}
          action={<Button variant="outline" onClick={() => location.reload()}>Try again</Button>} />
      ) : isLoading ? (
        <div className="space-y-3">{Array.from({ length: 4 }).map((_, i) => <CardSkeleton key={i} />)}</div>
      ) : jobs.length === 0 ? (
        <RadarEmpty
          title={filters.q || filters.family?.length || filters.location?.length || filters.posted_within_hours ? "No jobs match these filters" : "Your feed is clear"}
          body={filters.q || filters.family?.length || filters.location?.length || filters.posted_within_hours
            ? "Try removing a filter, or turn on “Show all roles”."
            : "Scout keeps scanning company job boards every few hours. New fresher roles will land here."}
        />
      ) : (
        <motion.div className="space-y-3" initial="hidden" animate="show"
          variants={{ hidden: {}, show: { transition: { staggerChildren: 0.04 } } }}>
          <AnimatePresence initial={false} mode="popLayout">
            {jobs.map((job, i) => (
              <JobCard
                key={job.id}
                ref={(el) => { cardRefs.current[i] = el }}
                job={job}
                isNew={isNew(job)}
                active={i === active}
                onHover={() => setActive(i)}
                onOpen={() => openJob(job)}
                onOpened={() => afterOpen(job)}
                onSkip={() => changeStatus(job, "skipped")}
                onApplied={() => changeStatus(job, "applied")}
                onDetails={() => openDetails(job.id)}
              />
            ))}
          </AnimatePresence>
          <div ref={sentinel} className="flex h-16 items-center justify-center text-sm text-muted-foreground">
            {isFetchingNextPage ? <Loader2 className="size-4 animate-spin" /> : !hasNextPage && jobs.length > 5 ? "That's everything for now." : null}
          </div>
        </motion.div>
      )}

      <JobSheet
        jobId={openJobId}
        preview={jobs.find((j) => j.id === openJobId)}
        onClose={() => openDetails(null)}
        onOpened={afterOpen}
        onSkip={(j) => changeStatus(j, "skipped")}
        onApplied={(j) => changeStatus(j, "applied")}
      />
    </div>
  )
}
