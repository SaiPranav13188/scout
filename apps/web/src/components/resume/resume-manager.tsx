"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  CircleCheck, FileUp, History, Loader2, Plus, RotateCcw, ShieldCheck, Sparkles, TriangleAlert, Upload,
} from "lucide-react"
import { motion } from "motion/react"
import { useEffect, useMemo, useRef, useState } from "react"
import { toast } from "sonner"

import { PageContainer, PageHeader } from "@/components/page-header"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { api, ApiError } from "@/lib/api"
import { timeAgo } from "@/lib/format"
import { keys } from "@/lib/queries"
import type { Contact, FactCheck, ResumeData, ResumeOverview, ResumeVersion } from "@/lib/types"
import { cn } from "@/lib/utils"

import {
  ChipList, describeFlag, Field, ItemCard, LineList, move, readFlags, RowActions, SectionCard, textFlagged, TextField,
} from "./fields"

type Draft = { base: number; data: ResumeData; contact: Contact }

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b)

export function ResumeManager() {
  const qc = useQueryClient()
  const { data: overview, isLoading, error } = useQuery({ queryKey: keys.resume, queryFn: api.resume })
  const active = overview?.active ?? null
  const [edits, setEdits] = useState<Draft | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)

  // The draft follows the active version; edits made on top of an older version are dropped.
  const draft: Draft | null = active
    ? edits && edits.base === active.version ? edits : { base: active.version, data: active.data, contact: active.contact }
    : null
  const dirty = Boolean(active && draft && !same({ d: draft.data, c: draft.contact }, { d: active.data, c: active.contact }))

  const setData = (fn: (d: ResumeData) => ResumeData) => draft && setEdits({ ...draft, data: fn(draft.data) })
  const setContact = (fn: (c: Contact) => Contact) => draft && setEdits({ ...draft, contact: fn(draft.contact) })

  // Live fact check while editing (debounced); the saved result is used when nothing has changed.
  const [live, setLive] = useState<{ key: string; result: FactCheck } | null>(null)
  const draftKey = draft ? JSON.stringify(draft.data) : ""
  useEffect(() => {
    if (!dirty || !draft || !active?.can_fact_check) return
    const t = setTimeout(() => {
      api.checkResume(draft.data).then((result) => setLive({ key: draftKey, result })).catch(() => {})
    }, 500)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- draftKey captures draft.data
  }, [draftKey, dirty, active?.can_fact_check])
  const check: FactCheck | null = !active ? null
    : !dirty ? active.fact_check
    : live && live.key === draftKey ? live.result : null
  const flags = useMemo(() => readFlags(check?.unsupported ?? []), [check])

  useEffect(() => {
    if (!dirty) return
    const warn = (e: BeforeUnloadEvent) => e.preventDefault()
    window.addEventListener("beforeunload", warn)
    return () => window.removeEventListener("beforeunload", warn)
  }, [dirty])

  const onResult = (res: ResumeOverview) => {
    qc.setQueryData(keys.resume, res)
    setEdits(null)
    qc.invalidateQueries({ queryKey: ["jobs"] })
  }
  const fail = (title: string) => (e: Error) =>
    toast.error(title, { description: e instanceof ApiError ? e.message : "The server couldn't be reached." })

  const save = useMutation({
    mutationFn: () => api.saveResume(draft!.base, draft!.data, draft!.contact),
    onSuccess: (res) => {
      onResult(res)
      if (res.saved === false) toast("No changes to save")
      else toast.success(`Saved as version ${res.active?.version}`, {
        description: res.scoring.outdated ? "Match scores will be refreshed over the next few runs." : undefined,
      })
    },
    onError: fail("Not saved"),
  })
  const activate = useMutation({
    mutationFn: (v: number) => api.activateResume(v),
    onSuccess: (res) => { onResult(res); toast.success(`Version ${res.active?.version} is now in use`) },
    onError: fail("Couldn't switch version"),
  })
  const upload = useMutation({
    mutationFn: (f: File) => api.uploadResume(f),
    onSuccess: (res) => {
      onResult(res)
      toast.success(`Parsed into version ${res.active?.version}`, { description: "Review it below, then save." })
    },
    onError: fail("Upload failed"),
  })

  const pickFile = () => {
    if (dirty && !confirm("You have unsaved changes. Upload a new resume and discard them?")) return
    fileInput.current?.click()
  }
  const busy = save.isPending || activate.isPending || upload.isPending

  const header = (
    <PageHeader title="Resume"
      description="Your master resume as Scout reads it: used for match scores now and tailored resumes later. Every save is a new version; earlier versions are never changed."
      actions={
        <>
          <input ref={fileInput} type="file" accept="application/pdf,.pdf" className="hidden"
            onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) upload.mutate(f) }} />
          <Button variant="outline" onClick={pickFile} disabled={busy}>
            {upload.isPending ? <Loader2 className="animate-spin" /> : <Upload />} Upload new PDF
          </Button>
        </>
      } />
  )

  if (isLoading) {
    return <PageContainer>{header}<div className="space-y-4">{[0, 1, 2].map((i) => <div key={i} className="skeleton-shimmer h-40 rounded-2xl" />)}</div></PageContainer>
  }
  if (error) {
    return <PageContainer>{header}<p className="text-sm text-destructive">Couldn&apos;t load your resume: {error.message}</p></PageContainer>
  }

  return (
    <PageContainer>
      {header}
      {upload.isPending && (
        <div role="status" className="flex items-center gap-3 rounded-2xl border border-brand/30 bg-brand-soft p-4 text-sm">
          <Loader2 className="size-4 animate-spin text-brand" />
          Reading your PDF and structuring it with Gemini. This usually takes 10-40 seconds. Contact details are removed before anything is sent.
        </div>
      )}

      {!active || !draft ? (
        <EmptyState onUpload={pickFile} busy={busy} />
      ) : (
        <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_280px]">
          <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="min-w-0 space-y-5">
            <ReviewBanner active={active} dirty={dirty} />
            <Editor draft={draft} flags={flags} setData={setData} setContact={setContact} />
          </motion.div>

          <aside aria-label="Resume status" className="space-y-4 lg:sticky lg:top-20 lg:self-start">
            <FactCheckCard check={check} canCheck={active.can_fact_check} dirty={dirty} />
            <ScoringCard scoring={overview!.scoring} />
            <VersionsCard overview={overview!} busy={busy} onActivate={(v) => {
              if (dirty && !confirm("You have unsaved changes. Switch versions and discard them?")) return
              activate.mutate(v)
            }} />
          </aside>
        </div>
      )}

      {active && (dirty || !active.reviewed) && (
        <motion.div initial={{ y: 40, opacity: 0 }} animate={{ y: 0, opacity: 1 }}
          className="sticky bottom-4 z-20 flex flex-wrap items-center justify-between gap-3 rounded-2xl border bg-background/90 p-3 pl-5 shadow-lg backdrop-blur-xl">
          <p className="text-sm">
            {dirty ? <>Unsaved changes. Saving creates <b>version {Math.max(...overview!.versions.map((v) => v.version)) + 1}</b>; version {active.version} stays as it is.</>
              : <>Check each section against your PDF. If it&apos;s all right, confirm it once.</>}
          </p>
          <div className="flex gap-2">
            {dirty && <Button variant="ghost" onClick={() => setEdits(null)} disabled={busy}><RotateCcw /> Discard</Button>}
            <Button className="bg-brand text-brand-foreground hover:bg-brand/90" onClick={() => save.mutate()} disabled={busy}>
              {save.isPending ? <Loader2 className="animate-spin" /> : <CircleCheck />}
              {dirty ? "Save and mark reviewed" : "Looks right, mark reviewed"}
            </Button>
          </div>
        </motion.div>
      )}
    </PageContainer>
  )
}

function EmptyState({ onUpload, busy }: { onUpload: () => void; busy: boolean }) {
  return (
    <div className="grid place-items-center rounded-2xl border border-dashed bg-card/40 px-6 py-16 text-center">
      <div className="grid size-12 place-items-center rounded-xl bg-brand-soft text-brand"><FileUp className="size-5" /></div>
      <h2 className="mt-4 font-medium">No resume yet</h2>
      <p className="mt-1 max-w-sm text-sm text-muted-foreground">
        Upload your resume as a PDF (up to 5 MB). Scout splits it into sections for you to check once.
      </p>
      <Button className="mt-5 bg-brand text-brand-foreground hover:bg-brand/90" onClick={onUpload} disabled={busy}><Upload /> Upload PDF</Button>
    </div>
  )
}

function ReviewBanner({ active, dirty }: { active: ResumeVersion; dirty: boolean }) {
  const reviewed = active.reviewed && !dirty
  return (
    <div className={cn("flex items-start gap-3 rounded-2xl border p-4 text-sm",
      reviewed ? "border-success/30 bg-success-soft" : "border-warning/30 bg-warning-soft")}>
      {reviewed ? <ShieldCheck className="mt-0.5 size-4 shrink-0 text-success" /> : <Sparkles className="mt-0.5 size-4 shrink-0 text-warning" />}
      <div>
        <p className="font-medium">
          Version {active.version} · {reviewed ? "reviewed by you" : "not reviewed yet"}
        </p>
        <p className="text-muted-foreground">
          {active.source}{active.parsed_with && !active.edited_from ? ` with ${active.parsed_with}` : ""}, {timeAgo(active.uploaded_at)}.
          {!reviewed && " The parser copies wording from your PDF but can still misplace things. Fix anything that's wrong."}
        </p>
      </div>
    </div>
  )
}

function FactCheckCard({ check, canCheck, dirty }: { check: FactCheck | null; canCheck: boolean; dirty: boolean }) {
  return (
    <div className="rounded-2xl border bg-card/60 p-4">
      <h3 className="flex items-center gap-2 text-sm font-medium"><ShieldCheck className="size-4 text-brand" /> Fact check</h3>
      {!canCheck ? (
        <p className="mt-2 text-xs text-muted-foreground">The original file text isn&apos;t stored for this version, so edits can&apos;t be checked against it.</p>
      ) : !check ? (
        <p className="mt-2 flex items-center gap-2 text-xs text-muted-foreground"><Loader2 className="size-3 animate-spin" /> Checking…</p>
      ) : check.ok ? (
        <p className="mt-2 text-xs text-muted-foreground">
          Every skill, tool, number and date {dirty ? "in your edits" : "here"} appears in your uploaded file.
        </p>
      ) : (
        <>
          <p className="mt-2 text-xs text-muted-foreground">
            Not found in your uploaded file. Fine if you added it on purpose and it&apos;s true; tailored resumes will treat it as a fact about you.
          </p>
          <ul className="mt-2 space-y-1.5">
            {check.unsupported.map((u) => (
              <li key={u} className="flex gap-1.5 text-xs text-warning">
                <TriangleAlert className="mt-px size-3 shrink-0" /><span className="text-foreground">{describeFlag(u)}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  )
}

function ScoringCard({ scoring }: { scoring: ResumeOverview["scoring"] }) {
  const total = scoring.current + scoring.outdated + scoring.unscored
  return (
    <div className="rounded-2xl border bg-card/60 p-4">
      <h3 className="text-sm font-medium">Match scores in your feed</h3>
      {total === 0 ? (
        <p className="mt-2 text-xs text-muted-foreground">No open jobs in the feed right now.</p>
      ) : (
        <>
          <div className="mt-3 flex h-1.5 overflow-hidden rounded-full bg-secondary">
            <div className="bg-success" style={{ width: `${(100 * scoring.current) / total}%` }} />
            <div className="bg-warning" style={{ width: `${(100 * scoring.outdated) / total}%` }} />
          </div>
          <dl className="mt-3 space-y-1 text-xs">
            {([["Scored with this resume", scoring.current, "bg-success"], ["Scored with an older version", scoring.outdated, "bg-warning"],
              ["Not scored yet", scoring.unscored, "bg-secondary"]] as const).map(([label, n, dot]) => (
              <div key={label} className="flex items-center justify-between gap-2">
                <dt className="flex items-center gap-1.5 text-muted-foreground"><span className={cn("size-2 rounded-full", dot)} />{label}</dt>
                <dd className="tabular-nums">{n}</dd>
              </div>
            ))}
          </dl>
          {scoring.outdated + scoring.unscored > 0 && (
            <p className="mt-2 text-xs text-muted-foreground">Scored a few at a time on each run, within Gemini&apos;s free daily limit.</p>
          )}
        </>
      )}
    </div>
  )
}

function VersionsCard({ overview, busy, onActivate }: { overview: ResumeOverview; busy: boolean; onActivate: (v: number) => void }) {
  return (
    <div className="rounded-2xl border bg-card/60 p-4">
      <h3 className="flex items-center gap-2 text-sm font-medium"><History className="size-4 text-brand" /> Versions</h3>
      <ol className="mt-3 space-y-2">
        {overview.versions.map((v) => (
          <li key={v.version} className={cn("rounded-lg border p-2.5 text-xs", v.is_active ? "border-brand/40 bg-brand-soft" : "bg-background/40")}>
            <div className="flex items-center justify-between gap-2">
              <span className="font-medium">Version {v.version}</span>
              {v.is_active ? <span className="font-medium text-brand">In use</span>
                : <Button variant="ghost" size="xs" disabled={busy} onClick={() => onActivate(v.version)}>Use this</Button>}
            </div>
            <p className="mt-0.5 text-muted-foreground">{v.source} · {timeAgo(v.uploaded_at)}</p>
            <p className="mt-1 flex flex-wrap gap-x-2 text-muted-foreground">
              <span className={v.reviewed ? "text-success" : undefined}>{v.reviewed ? "Reviewed" : "Not reviewed"}</span>
              {v.unsupported > 0 && <span className="text-warning">{v.unsupported} not in file</span>}
            </p>
          </li>
        ))}
      </ol>
    </div>
  )
}

function Editor({ draft, flags, setData, setContact }: {
  draft: Draft
  flags: ReturnType<typeof readFlags>
  setData: (fn: (d: ResumeData) => ResumeData) => void
  setContact: (fn: (c: Contact) => Contact) => void
}) {
  const d = draft.data
  const c = draft.contact
  const list = <K extends "education" | "skills" | "projects" | "experience">(key: K) => ({
    set: (i: number, v: ResumeData[K][number]) => setData((x) => ({ ...x, [key]: x[key].map((it, j) => (j === i ? v : it)) })),
    move: (a: number, b: number) => setData((x) => ({ ...x, [key]: move(x[key] as unknown[], a, b) })),
    remove: (i: number) => setData((x) => ({ ...x, [key]: x[key].filter((_, j) => j !== i) })),
  })
  const edu = list("education"), skills = list("skills"), projects = list("projects"), exp = list("experience")

  return (
    <div className="space-y-5">
      <SectionCard title="Contact" description="Kept on this server only. Never sent to Gemini; added back when a resume is rendered.">
        <div className="grid gap-4 sm:grid-cols-2">
          <TextField label="Name" value={c.name} onChange={(v) => setContact((x) => ({ ...x, name: v }))} />
          <Field label="Email"><ChipList label="Emails" items={c.emails} placeholder="you@example.com" onChange={(v) => setContact((x) => ({ ...x, emails: v }))} /></Field>
          <Field label="Phone"><ChipList label="Phones" items={c.phones} placeholder="+91 …" onChange={(v) => setContact((x) => ({ ...x, phones: v }))} /></Field>
          <Field label="Links"><ChipList label="Links" items={c.links} placeholder="linkedin.com/in/…" onChange={(v) => setContact((x) => ({ ...x, links: v }))} /></Field>
        </div>
      </SectionCard>

      <SectionCard title="Headline and summary" description="Leave empty if your resume doesn't have them.">
        <TextField label="Headline" value={d.headline} flagged={textFlagged(flags, d.headline)}
          onChange={(v) => setData((x) => ({ ...x, headline: v }))} />
        <Field label="Summary">
          <Textarea value={d.summary ?? ""} rows={2}
            className={cn(textFlagged(flags, d.summary) && "border-warning ring-3 ring-warning/20")}
            onChange={(e) => setData((x) => ({ ...x, summary: e.target.value || null }))} />
        </Field>
      </SectionCard>

      <SectionCard title="Skills" count={d.skills.reduce((n, g) => n + g.items.length, 0)}
        description="Grouped the way your resume groups them. Matching uses all of them."
        action={<AddButton onClick={() => setData((x) => ({ ...x, skills: [...x.skills, { category: "", items: [] }] }))}>Group</AddButton>}>
        {d.skills.map((g, i) => (
          <div key={i} className="grid gap-2 sm:grid-cols-[160px_minmax(0,1fr)_auto] sm:items-start">
            <textarea value={g.category} placeholder="Category" aria-label={`Skill group ${i + 1} name`} rows={1}
              className="field-sizing-content min-h-8 resize-none rounded-lg border border-transparent bg-transparent px-2 py-1.5 text-sm font-medium outline-none hover:border-input focus:border-ring"
              onChange={(e) => skills.set(i, { ...g, category: e.target.value.replace(/\n/g, " ") })} />
            <ChipList label={`${g.category || "Skill group"} skills`} items={g.items} placeholder="Type a skill, press Enter"
              isFlagged={(s) => flags.skills.has(s.toLowerCase())} onChange={(items) => skills.set(i, { ...g, items })} />
            <RowActions index={i} count={d.skills.length} what="skill group" onMove={skills.move} onRemove={skills.remove} />
          </div>
        ))}
      </SectionCard>

      <SectionCard title="Projects" count={d.projects.length}
        action={<AddButton onClick={() => setData((x) => ({ ...x, projects: [...x.projects, { name: "", tech: [], dates: null, link_label: null, bullets: [""] }] }))}>Project</AddButton>}>
        {d.projects.map((p, i) => (
          <ItemCard key={i} title={p.name || "New project"} actions={<RowActions index={i} count={d.projects.length} what="project" onMove={projects.move} onRemove={projects.remove} />}>
            <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_140px]">
              <TextField label="Name" value={p.name} onChange={(v) => projects.set(i, { ...p, name: v ?? "" })} />
              <TextField label="Dates" value={p.dates} flagged={textFlagged(flags, p.dates)} onChange={(v) => projects.set(i, { ...p, dates: v })} />
            </div>
            <Field label="Tech used">
              <ChipList label={`${p.name} tech`} items={p.tech} placeholder="Type a tool, press Enter"
                isFlagged={(t) => flags.tech.get(p.name)?.has(t.toLowerCase()) ?? false}
                onChange={(tech) => projects.set(i, { ...p, tech })} />
            </Field>
            <Field label="Bullets">
              <LineList items={p.bullets} flags={flags} what="bullet" addLabel="Bullet" onChange={(bullets) => projects.set(i, { ...p, bullets })} />
            </Field>
          </ItemCard>
        ))}
      </SectionCard>

      <SectionCard title="Experience" count={d.experience.length} description="Internships and jobs. Empty is fine for a fresher."
        action={<AddButton onClick={() => setData((x) => ({ ...x, experience: [...x.experience, { organization: "", role: "", start: null, end: null, location: null, bullets: [""] }] }))}>Role</AddButton>}>
        {d.experience.length === 0 && <p className="text-sm text-muted-foreground">No experience listed.</p>}
        {d.experience.map((e, i) => (
          <ItemCard key={i} title={[e.role, e.organization].filter(Boolean).join(" · ") || "New role"} actions={<RowActions index={i} count={d.experience.length} what="role" onMove={exp.move} onRemove={exp.remove} />}>
            <div className="grid gap-3 sm:grid-cols-2">
              <TextField label="Role" value={e.role} onChange={(v) => exp.set(i, { ...e, role: v ?? "" })} />
              <TextField label="Organization" value={e.organization} onChange={(v) => exp.set(i, { ...e, organization: v ?? "" })} />
              <TextField label="Start" value={e.start} flagged={textFlagged(flags, e.start)} onChange={(v) => exp.set(i, { ...e, start: v })} />
              <TextField label="End" value={e.end} placeholder="Present" flagged={textFlagged(flags, e.end)} onChange={(v) => exp.set(i, { ...e, end: v })} />
              <TextField label="Location" value={e.location} onChange={(v) => exp.set(i, { ...e, location: v })} />
            </div>
            <Field label="Bullets">
              <LineList items={e.bullets} flags={flags} what="bullet" addLabel="Bullet" onChange={(bullets) => exp.set(i, { ...e, bullets })} />
            </Field>
          </ItemCard>
        ))}
      </SectionCard>

      <SectionCard title="Education" count={d.education.length}
        action={<AddButton onClick={() => setData((x) => ({ ...x, education: [...x.education, { institution: "", degree: null, field: null, start: null, end: null, grade: null }] }))}>Education</AddButton>}>
        {d.education.map((e, i) => (
          <ItemCard key={i} title={e.institution || "New education"} actions={<RowActions index={i} count={d.education.length} what="education" onMove={edu.move} onRemove={edu.remove} />}>
            <div className="grid gap-3 sm:grid-cols-2">
              <TextField label="Institution" className="sm:col-span-2" value={e.institution} onChange={(v) => edu.set(i, { ...e, institution: v ?? "" })} />
              <TextField label="Degree" value={e.degree} onChange={(v) => edu.set(i, { ...e, degree: v })} />
              <TextField label="Field" value={e.field} onChange={(v) => edu.set(i, { ...e, field: v })} />
              <TextField label="Start" value={e.start} flagged={textFlagged(flags, e.start)} onChange={(v) => edu.set(i, { ...e, start: v })} />
              <TextField label="End" value={e.end} flagged={textFlagged(flags, e.end)} onChange={(v) => edu.set(i, { ...e, end: v })} />
              <TextField label="Grade" value={e.grade} placeholder="CGPA 8.5" flagged={textFlagged(flags, e.grade)} onChange={(v) => edu.set(i, { ...e, grade: v })} />
            </div>
          </ItemCard>
        ))}
      </SectionCard>

      <SectionCard title="Achievements" count={d.achievements.length}>
        <LineList items={d.achievements} flags={flags} what="achievement" addLabel="Achievement" onChange={(v) => setData((x) => ({ ...x, achievements: v }))} />
      </SectionCard>

      <SectionCard title="Certifications" count={d.certifications.length}>
        <LineList items={d.certifications} flags={flags} what="certification" addLabel="Certification" multiline={false}
          onChange={(v) => setData((x) => ({ ...x, certifications: v }))} />
      </SectionCard>
    </div>
  )
}

function AddButton({ onClick, children }: { onClick: () => void; children: React.ReactNode }) {
  return <Button type="button" variant="outline" size="sm" onClick={onClick}><Plus /> {children}</Button>
}
