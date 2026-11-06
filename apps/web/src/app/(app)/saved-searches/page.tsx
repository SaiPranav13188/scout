"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { AlertTriangle, ArrowUpRight, Loader2, Pencil, Plus, Trash2 } from "lucide-react"
import { AnimatePresence, motion } from "motion/react"
import { useState } from "react"
import { toast } from "sonner"

import { fadeUp, stagger } from "@/components/motion"
import { PageContainer, PageHeader } from "@/components/page-header"
import { RadarEmpty } from "@/components/today/empty-state"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { api } from "@/lib/api"
import { keys } from "@/lib/queries"
import type { SavedSearch } from "@/lib/types"

const SITES: Record<string, { name: string; color: string; letter: string }> = {
  linkedin: { name: "LinkedIn", color: "#0a66c2", letter: "in" },
  wellfound: { name: "Wellfound", color: "#e5e5e5", letter: "W" },
  internshala: { name: "Internshala", color: "#00a5ec", letter: "IS" },
  instahyre: { name: "Instahyre", color: "#1fb6ff", letter: "IH" },
  cutshort: { name: "Cutshort", color: "#6c4cf5", letter: "CS" },
  yc: { name: "Work at a Startup", color: "#f26522", letter: "Y" },
  naukri: { name: "Naukri", color: "#4a90e2", letter: "N" },
  other: { name: "Other", color: "#888", letter: "•" },
}

/** Links that open a site's home page or a profile instead of a saved search. */
function linkProblem(s: { site: string; url: string }): string | null {
  const u = s.url.toLowerCase()
  if (u.includes("linkedin.com/in/")) return "This is a LinkedIn profile, not a job search."
  if (s.site === "linkedin" && !u.includes("/jobs/search")) {
    return "This opens LinkedIn's jobs home, not your search. Run the search, then copy the address bar (it contains /jobs/search/?keywords=…)."
  }
  return null
}

function SiteIcon({ site }: { site: string }) {
  const s = SITES[site] ?? SITES.other
  return (
    <div className="grid size-10 shrink-0 place-items-center rounded-xl text-xs font-bold ring-1 ring-white/10"
      style={{ background: `color-mix(in oklch, ${s.color} 18%, transparent)`, color: s.color }}>
      {s.letter}
    </div>
  )
}

type Draft = { site: string; label: string; url: string }

/** One form for adding and editing. `editing` = the search being edited, or null to add. */
function SearchDialog({ open, onOpenChange, editing }: {
  open: boolean
  onOpenChange: (v: boolean) => void
  editing: SavedSearch | null
}) {
  const qc = useQueryClient()
  const [draft, setDraft] = useState<Draft>({ site: "linkedin", label: "", url: "" })
  const [lastKey, setLastKey] = useState<string | null>(null)
  // Reset the form whenever the dialog opens for a different search (or for "add").
  const key = open ? (editing?.id ?? "new") : null
  if (key !== lastKey) {
    setLastKey(key)
    if (open) setDraft(editing ? { site: editing.site, label: editing.label, url: editing.url } : { site: "linkedin", label: "", url: "" })
  }

  const save = useMutation({
    mutationFn: () => (editing ? api.updateSavedSearch(editing.id, draft) : api.addSavedSearch(draft)),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.saved })
      toast.success(editing ? "Saved search updated" : "Saved search added")
      onOpenChange(false)
    },
    onError: (e) => toast.error("Couldn't save it", { description: e.message }),
  })
  const problem = draft.url ? linkProblem(draft) : null

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{editing ? "Edit saved search" : "Add a saved search"}</DialogTitle>
          <DialogDescription>
            Run the search on the site, then copy the address bar. Scout only stores the link; it never logs in or scrapes.
          </DialogDescription>
        </DialogHeader>
        <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); save.mutate() }}>
          <div className="space-y-2">
            <Label>Site</Label>
            <Select value={draft.site} onValueChange={(site) => setDraft({ ...draft, site })}>
              <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
              <SelectContent>
                {Object.entries(SITES).map(([k, v]) => <SelectItem key={k} value={k}>{v.name}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label htmlFor="label">Label</Label>
            <Input id="label" required value={draft.label} onChange={(e) => setDraft({ ...draft, label: e.target.value })}
              placeholder="LinkedIn – AI / Backend / Full Stack – India" />
          </div>
          <div className="space-y-2">
            <Label htmlFor="url">URL</Label>
            <Input id="url" type="url" required value={draft.url} onChange={(e) => setDraft({ ...draft, url: e.target.value })}
              placeholder="https://…" />
            <AnimatePresence>
              {problem && (
                <motion.p initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }}
                  className="flex gap-1.5 text-xs text-warning">
                  <AlertTriangle className="mt-0.5 size-3.5 shrink-0" /> {problem}
                </motion.p>
              )}
            </AnimatePresence>
          </div>
          <Button type="submit" disabled={save.isPending} className="w-full">
            {save.isPending ? <Loader2 className="animate-spin" /> : editing ? "Save changes" : "Save"}
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function DeleteDialog({ search, onOpenChange, onConfirm }: {
  search: SavedSearch | null
  onOpenChange: (v: boolean) => void
  onConfirm: (s: SavedSearch) => void
}) {
  return (
    <Dialog open={Boolean(search)} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Delete this saved search?</DialogTitle>
          <DialogDescription className="break-words">
            “{search?.label}” will be removed from Scout. The search itself on {search && search.site !== "other" ? SITES[search.site]?.name : "the job site"} isn’t affected.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button variant="destructive" onClick={() => search && onConfirm(search)}><Trash2 /> Delete</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export default function SavedSearchesPage() {
  const qc = useQueryClient()
  const { data, isLoading } = useQuery({ queryKey: keys.saved, queryFn: api.savedSearches })
  const [formOpen, setFormOpen] = useState(false)
  const [editing, setEditing] = useState<SavedSearch | null>(null)
  const [deleting, setDeleting] = useState<SavedSearch | null>(null)

  const restore = useMutation({
    mutationFn: (s: SavedSearch) => api.addSavedSearch({ site: s.site, label: s.label, url: s.url }),
    onSettled: () => qc.invalidateQueries({ queryKey: keys.saved }),
  })
  const del = useMutation({
    mutationFn: (s: SavedSearch) => api.deleteSavedSearch(s.id),
    onMutate: async (s) => {
      await qc.cancelQueries({ queryKey: keys.saved })
      const prev = qc.getQueryData<SavedSearch[]>(keys.saved)
      qc.setQueryData<SavedSearch[]>(keys.saved, (d) => d?.filter((x) => x.id !== s.id))
      return { prev }
    },
    onSuccess: (_r, s) => {
      toast("Saved search deleted", {
        description: s.label,
        action: { label: "Undo", onClick: () => restore.mutate(s) },
        duration: 8000,
      })
    },
    onError: (e, _s, ctx) => {
      qc.setQueryData(keys.saved, ctx?.prev)
      toast.error("Couldn't delete it", { description: e.message })
    },
    onSettled: () => qc.invalidateQueries({ queryKey: keys.saved }),
  })

  const openAdd = () => { setEditing(null); setFormOpen(true) }
  const addButton = (
    <Button onClick={openAdd} className="bg-brand text-brand-foreground hover:bg-brand/90"><Plus /> Add search</Button>
  )

  return (
    <PageContainer>
      <PageHeader
        title="Saved searches"
        description="One-click links to your searches on LinkedIn, Wellfound, Internshala, Instahyre, Cutshort and YC. You apply there yourself."
        actions={addButton}
      />
      {isLoading ? (
        <div className="grid gap-3 sm:grid-cols-2">{Array.from({ length: 4 }).map((_, i) => <div key={i} className="skeleton-shimmer h-20 rounded-xl" />)}</div>
      ) : !data?.length ? (
        <RadarEmpty title="No saved searches yet" body="Add the search links you use on each job site. They'll sit here, one click away." action={addButton} />
      ) : (
        <motion.div variants={stagger(0.04)} initial="hidden" animate="show" className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <AnimatePresence mode="popLayout">
            {data.map((s) => {
              const problem = linkProblem(s)
              return (
                <motion.div
                  key={s.id}
                  layout
                  variants={fadeUp}
                  exit={{ opacity: 0, scale: 0.95, transition: { duration: 0.2 } }}
                  whileHover={{ y: -2 }}
                  className="group flex min-w-0 items-center gap-3 rounded-xl border bg-card/60 p-3 transition-colors hover:border-foreground/20 sm:p-4"
                >
                  <a href={s.url} target="_blank" rel="noopener noreferrer" className="flex min-w-0 flex-1 items-center gap-3">
                    <SiteIcon site={s.site} />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium">{s.label}</p>
                      <p className="flex items-center gap-1.5 truncate text-xs text-muted-foreground">
                        {SITES[s.site]?.name ?? s.site}
                        {problem && (
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <span className="inline-flex items-center gap-1 rounded bg-warning-soft px-1.5 text-[10px] font-medium text-warning">
                                <AlertTriangle className="size-3" /> Not a search link
                              </span>
                            </TooltipTrigger>
                            <TooltipContent className="max-w-xs">{problem}</TooltipContent>
                          </Tooltip>
                        )}
                      </p>
                    </div>
                    <ArrowUpRight className="size-4 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5 group-hover:-translate-y-0.5 group-hover:text-foreground" />
                  </a>
                  <div className="flex shrink-0 items-center gap-0.5 border-l pl-2">
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Button variant="ghost" size="icon-sm" aria-label={`Edit ${s.label}`}
                          onClick={() => { setEditing(s); setFormOpen(true) }}>
                          <Pencil />
                        </Button>
                      </TooltipTrigger>
                      <TooltipContent>Edit</TooltipContent>
                    </Tooltip>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Button variant="ghost" size="icon-sm" aria-label={`Delete ${s.label}`}
                          className="text-muted-foreground hover:bg-danger-soft hover:text-danger"
                          onClick={() => setDeleting(s)}>
                          <Trash2 />
                        </Button>
                      </TooltipTrigger>
                      <TooltipContent>Delete</TooltipContent>
                    </Tooltip>
                  </div>
                </motion.div>
              )
            })}
          </AnimatePresence>
        </motion.div>
      )}

      <SearchDialog open={formOpen} onOpenChange={setFormOpen} editing={editing} />
      <DeleteDialog
        search={deleting}
        onOpenChange={(v) => !v && setDeleting(null)}
        onConfirm={(s) => { setDeleting(null); del.mutate(s) }}
      />
    </PageContainer>
  )
}
