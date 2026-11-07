import type { LocationBucket, RoleFamily, TrustLevel } from "./types"

const rtf = new Intl.RelativeTimeFormat("en", { numeric: "auto", style: "short" })

export function timeAgo(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "date unknown"
  const diff = (new Date(iso).getTime() - now) / 1000
  const abs = Math.abs(diff)
  if (abs < 60) return "just now"
  if (abs < 3600) return rtf.format(Math.round(diff / 60), "minute")
  if (abs < 86400) return rtf.format(Math.round(diff / 3600), "hour")
  if (abs < 86400 * 30) return rtf.format(Math.round(diff / 86400), "day")
  return new Date(iso).toLocaleDateString("en-IN", { day: "numeric", month: "short" })
}

export function hoursSince(iso: string | null | undefined): number {
  if (!iso) return Infinity
  return (Date.now() - new Date(iso).getTime()) / 3_600_000
}

export const FAMILY_LABEL: Record<RoleFamily, string> = {
  applied_ai: "Applied AI",
  fde: "Forward Deployed",
  backend: "Backend",
  fullstack: "Full-stack",
  software: "Software",
  other: "Other",
  unrelated: "Unrelated",
}

export const LOCATION_LABEL: Record<LocationBucket, string> = {
  india: "India",
  remote_global: "Remote",
  remote_restricted: "Remote (abroad only)",
  abroad: "Abroad",
  unknown: "Not stated",
}

export const SOURCE_LABEL: Record<string, string> = {
  greenhouse: "Greenhouse",
  lever: "Lever",
  ashby: "Ashby",
  adzuna: "Adzuna",
  linkedin_alert: "LinkedIn alert",
  wellfound_alert: "Wellfound alert",
  internshala_alert: "Internshala alert",
  instahyre_alert: "Instahyre alert",
}

export const TRUST_META: Record<TrustLevel, { label: string; tone: string; hint: string }> = {
  verified: {
    label: "Verified",
    tone: "text-success bg-success-soft ring-success/25",
    hint: "Strong signals the company and posting are real. Not a guarantee.",
  },
  unverified: {
    label: "Unverified",
    tone: "text-muted-foreground bg-muted ring-border",
    hint: "Not enough evidence either way. Check the company before applying.",
  },
  caution: {
    label: "Caution",
    tone: "text-warning bg-warning-soft ring-warning/25",
    hint: "Some warning signs. Read the signals before applying.",
  },
  scam_risk: {
    label: "Scam risk",
    tone: "text-danger bg-danger-soft ring-danger/25",
    hint: "Hard red flags such as fees or deposits. Do not pay anyone to get a job.",
  },
}

export function initials(name: string) {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase())
    .join("")
}

/** Stable pleasant hue per company for its monogram. */
export function hueFor(name: string) {
  let h = 0
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) % 360
  return h
}

/** "Winter 2024" -> "W24" */
export function ycShort(batch: string) {
  const m = batch.match(/^(Winter|Summer|Spring|Fall)\s+\d{2}(\d{2})$/)
  return m ? `${m[1][0]}${m[2]}` : batch
}

/** Honest "when was this posted" label. Alert emails don't say, so we never pretend they do. */
export function postedLabel(job: { posted_at: string | null; posted_at_kind: string | null; first_seen_at: string }) {
  const seen = timeAgo(job.first_seen_at)
  if (!job.posted_at) {
    return {
      short: "Posted date not given",
      tooltip: `The job site's alert email didn't say when this was posted. Scout first saw it ${seen}.`,
    }
  }
  const when = new Date(job.posted_at).toLocaleString("en-IN")
  if (job.posted_at_kind === "updated") return { short: `updated ${timeAgo(job.posted_at)}`, tooltip: `Last updated ${when} (the site gives no original posting date).` }
  if (job.posted_at_kind === "relative_estimate") return { short: `~${timeAgo(job.posted_at)}`, tooltip: `Estimated from the site's "posted N days ago" text: around ${when}.` }
  return { short: `posted ${timeAgo(job.posted_at)}`, tooltip: `Posted ${when}` }
}
