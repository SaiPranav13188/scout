"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { ArrowUpRight, Loader2, Plus, Trash2 } from "lucide-react"
import { AnimatePresence, motion } from "motion/react"
import { useState } from "react"
import { toast } from "sonner"

import { fadeUp, stagger } from "@/components/motion"
import { PageContainer, PageHeader } from "@/components/page-header"
import { RadarEmpty } from "@/components/today/empty-state"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
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

function SiteIcon({ site }: { site: string }) {
  const s = SITES[site] ?? SITES.other
  return (
    <div className="grid size-10 shrink-0 place-items-center rounded-xl text-xs font-bold ring-1 ring-white/10"
      style={{ background: `color-mix(in oklch, ${s.color} 18%, transparent)`, color: s.color }}>
      {s.letter}
    </div>
  )
}

function AddDialog() {
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [site, setSite] = useState("linkedin")
  const [label, setLabel] = useState("")
  const [url, setUrl] = useState("")
  const add = useMutation({
    mutationFn: () => api.addSavedSearch({ site, label, url }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.saved })
      toast.success("Saved search added")
      setOpen(false)
      setLabel("")
      setUrl("")
    },
    onError: (e) => toast.error("Couldn't add it", { description: e.message }),
  })
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button className="bg-brand text-brand-foreground hover:bg-brand/90"><Plus /> Add search</Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add a saved search</DialogTitle>
          <DialogDescription>Paste the URL of a search you saved on the site. Scout only stores the link; it never logs in or scrapes.</DialogDescription>
        </DialogHeader>
        <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); add.mutate() }}>
          <div className="space-y-2">
            <Label>Site</Label>
            <Select value={site} onValueChange={setSite}>
              <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
              <SelectContent>
                {Object.entries(SITES).map(([k, v]) => <SelectItem key={k} value={k}>{v.name}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label htmlFor="label">Label</Label>
            <Input id="label" required value={label} onChange={(e) => setLabel(e.target.value)} placeholder="AI engineer · Bengaluru · entry level" />
          </div>
          <div className="space-y-2">
            <Label htmlFor="url">URL</Label>
            <Input id="url" type="url" required value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://…" />
          </div>
          <Button type="submit" disabled={add.isPending} className="w-full">
            {add.isPending ? <Loader2 className="animate-spin" /> : "Save"}
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  )
}

export default function SavedSearchesPage() {
  const qc = useQueryClient()
  const { data, isLoading } = useQuery({ queryKey: keys.saved, queryFn: api.savedSearches })
  const del = useMutation({
    mutationFn: (id: string) => api.deleteSavedSearch(id),
    onMutate: async (id) => {
      await qc.cancelQueries({ queryKey: keys.saved })
      const prev = qc.getQueryData<SavedSearch[]>(keys.saved)
      qc.setQueryData<SavedSearch[]>(keys.saved, (d) => d?.filter((s) => s.id !== id))
      return { prev }
    },
    onError: (_e, _id, ctx) => qc.setQueryData(keys.saved, ctx?.prev),
    onSettled: () => qc.invalidateQueries({ queryKey: keys.saved }),
  })

  return (
    <PageContainer>
      <PageHeader
        title="Saved searches"
        description="One-click links to your searches on LinkedIn, Wellfound, Internshala, Instahyre, Cutshort and YC. You apply there yourself."
        actions={<AddDialog />}
      />
      {isLoading ? (
        <div className="grid gap-3 sm:grid-cols-2">{Array.from({ length: 4 }).map((_, i) => <div key={i} className="skeleton-shimmer h-20 rounded-xl" />)}</div>
      ) : !data?.length ? (
        <RadarEmpty title="No saved searches yet" body="Add the search links you use on each job site. They'll sit here, one click away." action={<AddDialog />} />
      ) : (
        <motion.div variants={stagger(0.04)} initial="hidden" animate="show" className="grid gap-3 sm:grid-cols-2">
          <AnimatePresence mode="popLayout">
            {data.map((s) => (
              <motion.a
                key={s.id}
                layout
                variants={fadeUp}
                exit={{ opacity: 0, scale: 0.95 }}
                whileHover={{ y: -2 }}
                href={s.url}
                target="_blank"
                rel="noopener noreferrer"
                className="group flex items-center gap-3 rounded-xl border bg-card/60 p-4 transition-colors hover:border-foreground/20"
              >
                <SiteIcon site={s.site} />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">{s.label}</p>
                  <p className="truncate text-xs text-muted-foreground">{SITES[s.site]?.name ?? s.site}</p>
                </div>
                <button
                  onClick={(e) => { e.preventDefault(); del.mutate(s.id) }}
                  className="rounded-md p-1.5 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100 hover:bg-danger-soft hover:text-danger"
                  aria-label="Delete"
                >
                  <Trash2 className="size-4" />
                </button>
                <ArrowUpRight className="size-4 text-muted-foreground transition-transform group-hover:translate-x-0.5 group-hover:-translate-y-0.5 group-hover:text-foreground" />
              </motion.a>
            ))}
          </AnimatePresence>
        </motion.div>
      )}
    </PageContainer>
  )
}
