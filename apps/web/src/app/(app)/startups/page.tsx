"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { AlertCircle, ArrowUpRight, Loader2, Plus, Search } from "lucide-react"
import { motion } from "motion/react"
import { useMemo, useState } from "react"
import { toast } from "sonner"

import { fadeUp, stagger } from "@/components/motion"
import { PageContainer, PageHeader } from "@/components/page-header"
import { CompanyMark } from "@/components/today/badges"
import { RadarEmpty } from "@/components/today/empty-state"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { api } from "@/lib/api"
import { SOURCE_LABEL, timeAgo } from "@/lib/format"
import { keys } from "@/lib/queries"

function AddBoardDialog() {
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [url, setUrl] = useState("")
  const [name, setName] = useState("")
  const [website, setWebsite] = useState("")
  const add = useMutation({
    mutationFn: () => api.addBoard({ url, company_name: name, website: website || undefined }),
    onSuccess: (r) => {
      toast.success(`Watching ${name}`, { description: `${r.jobs_listed} jobs on its ${SOURCE_LABEL[r.ats]} board right now. They'll be filtered on the next sync.` })
      qc.invalidateQueries({ queryKey: keys.companies })
      qc.invalidateQueries({ queryKey: keys.stats })
      setOpen(false)
      setUrl(""); setName(""); setWebsite("")
    },
    onError: (e) => toast.error("Couldn't add that board", { description: e.message }),
  })
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button className="bg-brand text-brand-foreground hover:bg-brand/90"><Plus /> Add a company</Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Watch a dream company</DialogTitle>
          <DialogDescription>
            Paste the company&apos;s Greenhouse, Lever or Ashby job board link (e.g. jobs.lever.co/company). Scout checks it&apos;s live before adding.
          </DialogDescription>
        </DialogHeader>
        <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); add.mutate() }}>
          <div className="space-y-2">
            <Label htmlFor="b-url">Job board link</Label>
            <Input id="b-url" type="url" required value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://jobs.ashbyhq.com/…" />
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="b-name">Company name</Label>
              <Input id="b-name" required value={name} onChange={(e) => setName(e.target.value)} />
            </div>
            <div className="space-y-2">
              <Label htmlFor="b-web">Website (optional)</Label>
              <Input id="b-web" type="url" value={website} onChange={(e) => setWebsite(e.target.value)} placeholder="https://" />
            </div>
          </div>
          <Button type="submit" disabled={add.isPending} className="w-full">
            {add.isPending ? <><Loader2 className="animate-spin" /> Checking the board…</> : "Add company"}
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  )
}

export default function StartupsPage() {
  const { data, isLoading } = useQuery({ queryKey: keys.companies, queryFn: api.companies })
  const [q, setQ] = useState("")
  const rows = useMemo(() => {
    const s = q.trim().toLowerCase()
    return (data ?? []).filter((c) => !s || c.name.toLowerCase().includes(s) || c.description?.toLowerCase().includes(s))
  }, [data, q])

  return (
    <PageContainer>
      <PageHeader
        title="Startups"
        description="Companies Scout watches. Most were discovered automatically: their own website links to a public job board, and the board was confirmed live."
        actions={<AddBoardDialog />}
      />
      <div className="relative max-w-sm">
        <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter companies" className="h-9 pl-9" />
      </div>
      {isLoading ? (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">{Array.from({ length: 6 }).map((_, i) => <div key={i} className="skeleton-shimmer h-40 rounded-xl" />)}</div>
      ) : rows.length === 0 ? (
        <RadarEmpty title={q ? "No companies match" : "No companies yet"}
          body={q ? "Try a different name." : "Discovery runs weekly and adds companies whose job boards it can confirm. You can also add one yourself."} />
      ) : (
        <motion.div variants={stagger(0.025)} initial="hidden" animate="show" className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {rows.map((c) => (
            <motion.div key={`${c.id}-${c.board.token}`} variants={fadeUp} whileHover={{ y: -3 }}
              className="group flex flex-col rounded-xl border bg-card/60 p-4 transition-colors hover:border-foreground/20">
              <div className="flex items-start gap-3">
                <CompanyMark name={c.name} />
                <div className="min-w-0 flex-1">
                  <p className="truncate font-medium">{c.name}</p>
                  <p className="text-xs text-muted-foreground">
                    {[c.in_yc_directory && "YC", c.funding_stage, c.size && `${c.size} people`].filter(Boolean).join(" · ") || "Details unknown"}
                  </p>
                </div>
                {c.board.last_error && (
                  <Tooltip>
                    <TooltipTrigger><AlertCircle className="size-4 text-warning" /></TooltipTrigger>
                    <TooltipContent>{c.board.last_error}</TooltipContent>
                  </Tooltip>
                )}
              </div>
              <p className="mt-3 line-clamp-2 flex-1 text-sm text-muted-foreground">{c.description ?? "No description available."}</p>
              <div className="mt-4 flex items-center justify-between text-xs">
                <span className={c.open_fresher_jobs > 0 ? "font-medium text-success" : "text-muted-foreground"}>
                  {c.open_fresher_jobs} fresher {c.open_fresher_jobs === 1 ? "role" : "roles"}
                </span>
                <span className="text-muted-foreground">
                  {SOURCE_LABEL[c.board.ats]} · {c.board.last_fetched_at ? `synced ${timeAgo(c.board.last_fetched_at)}` : "not synced yet"}
                </span>
              </div>
              {(c.career_url || c.website) && (
                <a href={c.career_url ?? c.website!} target="_blank" rel="noopener noreferrer"
                  className="mt-3 inline-flex items-center gap-1 text-xs text-brand opacity-80 hover:underline group-hover:opacity-100">
                  Careers page <ArrowUpRight className="size-3" />
                </a>
              )}
            </motion.div>
          ))}
        </motion.div>
      )}
    </PageContainer>
  )
}
