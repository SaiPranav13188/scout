"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Bell, Clock, Filter, Pause, Plug } from "lucide-react"
import { motion } from "motion/react"
import { toast } from "sonner"

import { fadeUp, stagger } from "@/components/motion"
import { PageContainer, PageHeader } from "@/components/page-header"
import { Input } from "@/components/ui/input"
import { Switch } from "@/components/ui/switch"
import { api } from "@/lib/api"
import { keys } from "@/lib/queries"

type Settings = {
  schedule: Record<string, string | number>
  notifications: { digest_size: number; quiet_hours: { start: string; end: string } } & Record<string, unknown>
  sources: Record<string, boolean>
  fresher_rules: { max_experience_years: number; include_internships: boolean } & Record<string, unknown>
  preferences: { mandatory: Record<string, boolean> } & Record<string, unknown>
  paused: boolean
}

function Card({ icon: Icon, title, description, children }: {
  icon: React.ComponentType<{ className?: string }>
  title: string
  description: string
  children: React.ReactNode
}) {
  return (
    <motion.section variants={fadeUp} className="rounded-2xl border bg-card/60">
      <div className="flex items-start gap-3 border-b p-5">
        <div className="grid size-9 place-items-center rounded-lg bg-brand-soft text-brand"><Icon className="size-4" /></div>
        <div>
          <h2 className="font-medium">{title}</h2>
          <p className="text-sm text-muted-foreground">{description}</p>
        </div>
      </div>
      <div className="divide-y">{children}</div>
    </motion.section>
  )
}

function Row({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-4 px-5 py-4">
      <div className="min-w-0">
        <p className="text-sm">{label}</p>
        {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
      </div>
      {children}
    </div>
  )
}

export default function SettingsPage() {
  const qc = useQueryClient()
  const { data } = useQuery({ queryKey: keys.settings, queryFn: api.settings as () => Promise<Settings> })
  const save = useMutation({
    mutationFn: ({ key, value }: { key: string; value: unknown }) => api.putSetting(key, value),
    onMutate: async ({ key, value }) => {
      await qc.cancelQueries({ queryKey: keys.settings })
      const prev = qc.getQueryData<Settings>(keys.settings)
      qc.setQueryData<Settings>(keys.settings, (s) => (s ? { ...s, [key]: value } : s))
      return { prev }
    },
    onError: (e, _v, ctx) => { qc.setQueryData(keys.settings, ctx?.prev); toast.error("Not saved", { description: e.message }) },
    onSuccess: () => toast.success("Saved"),
    onSettled: () => { qc.invalidateQueries({ queryKey: keys.settings }); qc.invalidateQueries({ queryKey: ["jobs"] }) },
  })

  if (!data) {
    return <PageContainer><div className="space-y-4">{Array.from({ length: 3 }).map((_, i) => <div key={i} className="skeleton-shimmer h-48 rounded-2xl" />)}</div></PageContainer>
  }
  const set = (key: keyof Settings, value: unknown) => save.mutate({ key, value })

  return (
    <PageContainer>
      <PageHeader title="Settings" description="Changes apply on the next worker tick. No restart needed." />
      <motion.div variants={stagger(0.06)} initial="hidden" animate="show" className="space-y-5">
        <Card icon={Pause} title="Pause" description="Stops ingestion, digests and alerts until you resume. Same as /pause on Telegram.">
          <Row label="Paused" hint={data.paused ? "Nothing runs while paused." : "Scout is running on schedule."}>
            <Switch checked={data.paused} onCheckedChange={(v) => set("paused", v)} />
          </Row>
        </Card>

        <Card icon={Filter} title="Fresher filter" description="Plain rules, applied to every new job.">
          <Row label="Maximum experience (years)" hint="A job asking for more than this is dropped. 0-2 years is dropped at 1.">
            <Input type="number" min={0} max={5} step={0.5} className="h-8 w-20 text-right"
              defaultValue={data.fresher_rules.max_experience_years}
              onBlur={(e) => {
                const v = Number(e.target.value)
                if (v !== data.fresher_rules.max_experience_years) set("fresher_rules", { ...data.fresher_rules, max_experience_years: v })
              }} />
          </Row>
          <Row label="Include internships" hint="Intern-to-full-time roles (PPO) are always kept.">
            <Switch checked={data.fresher_rules.include_internships}
              onCheckedChange={(v) => set("fresher_rules", { ...data.fresher_rules, include_internships: v })} />
          </Row>
          <Row label="Location is mandatory" hint="On: only India, worldwide-remote, or unstated locations. Off: show everything, rank by fit.">
            <Switch checked={data.preferences.mandatory.location}
              onCheckedChange={(v) => set("preferences", { ...data.preferences, mandatory: { ...data.preferences.mandatory, location: v } })} />
          </Row>
        </Card>

        <Card icon={Plug} title="Sources" description="Each source is a plug-in. Gmail alerts and aggregators arrive in Phase 2.">
          {Object.entries(data.sources).map(([k, v]) => (
            <Row key={k} label={k[0].toUpperCase() + k.slice(1)} hint={k === "adzuna" ? "Needs an Adzuna API key in the server environment." : "Public company job boards."}>
              <Switch checked={v} onCheckedChange={(nv) => set("sources", { ...data.sources, [k]: nv })} />
            </Row>
          ))}
        </Card>

        <Card icon={Clock} title="Schedule" description={`Times are in ${data.schedule.timezone}.`}>
          <Row label="Check job boards every (minutes)" hint="The cloud scheduler runs at most every 5-10 minutes, so values below 10 don't make it faster.">
            <Input type="number" min={10} max={1440} step={5} className="h-8 w-20 text-right" defaultValue={data.schedule.ingest_interval_minutes}
              onBlur={(e) => { const v = Number(e.target.value); if (v !== data.schedule.ingest_interval_minutes) set("schedule", { ...data.schedule, ingest_interval_minutes: v }) }} />
          </Row>
          {(["daily_digest_time", "evening_summary_time"] as const).map((k) => (
            <Row key={k} label={k === "daily_digest_time" ? "Morning digest" : "Evening summary"}>
              <Input type="time" className="h-8 w-28" defaultValue={String(data.schedule[k])}
                onBlur={(e) => { if (e.target.value && e.target.value !== data.schedule[k]) set("schedule", { ...data.schedule, [k]: e.target.value }) }} />
            </Row>
          ))}
        </Card>

        <Card icon={Bell} title="Notifications" description="Telegram is the main channel.">
          <Row label="Instant alerts" hint="A Telegram message as soon as new relevant jobs are found (several are grouped into one message).">
            <Switch checked={Boolean(data.notifications.instant_alerts_enabled)}
              onCheckedChange={(v) => set("notifications", { ...data.notifications, instant_alerts_enabled: v })} />
          </Row>
          <Row label="Jobs per digest">
            <Input type="number" min={1} max={20} className="h-8 w-20 text-right" defaultValue={data.notifications.digest_size}
              onBlur={(e) => { const v = Number(e.target.value); if (v !== data.notifications.digest_size) set("notifications", { ...data.notifications, digest_size: v }) }} />
          </Row>
          <Row label="Quiet hours" hint="No instant alerts in this window.">
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              {(["start", "end"] as const).map((k, i) => (
                <span key={k} className="flex items-center gap-2">
                  {i === 1 && "to"}
                  <Input type="time" className="h-8 w-28" defaultValue={data.notifications.quiet_hours[k]}
                    onBlur={(e) => {
                      if (e.target.value && e.target.value !== data.notifications.quiet_hours[k])
                        set("notifications", { ...data.notifications, quiet_hours: { ...data.notifications.quiet_hours, [k]: e.target.value } })
                    }} />
                </span>
              ))}
            </div>
          </Row>
        </Card>
      </motion.div>
    </PageContainer>
  )
}
