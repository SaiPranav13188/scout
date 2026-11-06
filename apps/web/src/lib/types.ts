// Mirrors apps/api/src/scout_api/schemas.py. Keep in sync.

export type TrustLevel = "verified" | "unverified" | "caution" | "scam_risk"
export type JobStatus =
  | "discovered" | "qualified" | "resume_ready" | "applied"
  | "interview" | "rejected" | "offer" | "skipped"
export type LocationBucket = "india" | "remote_global" | "remote_restricted" | "abroad" | "unknown"
export type RoleFamily = "applied_ai" | "fde" | "backend" | "fullstack" | "software" | "other" | "unrelated"

export interface TrustSignal {
  code: string
  weight: number
  detail: string
}

export interface CompanyBrief {
  id: string | null
  name: string
  website: string | null
  description: string | null
  funding_stage: string | null
  size: string | null
  hq: string | null
  in_yc_directory: boolean
  yc_batch: string | null
}

export interface Job {
  id: string
  title: string
  company: CompanyBrief
  location: string | null
  locations: string[]
  location_bucket: LocationBucket
  remote: boolean | null
  salary_text: string | null
  employment_type: string | null
  department: string | null
  source: string
  apply_url: string
  posted_at: string | null
  posted_at_kind: string | null
  first_seen_at: string
  is_new: boolean
  fresher_confidence: "high" | "medium" | "low" | null
  fresher_reasons: string[]
  experience_unclear: boolean
  experience_min: number | null
  experience_max: number | null
  role_family: RoleFamily
  relevance_score: number
  relevance_matched: string[]
  trust: { level: TrustLevel; score: number; signals: TrustSignal[] }
  verify_status: string | null
  verify_reason: string | null
  verified_at: string | null
  is_open: boolean | null
  status: JobStatus
  why: string
  my_label: JobLabel | null
}

export type JobLabel = "apply" | "maybe" | "no"

export interface LabelSummary {
  apply: number
  maybe: number
  no: number
  total: number
  target: number
}

export interface JobDetail extends Job {
  description: string | null
  filter_reasons: string[]
}

export interface JobPage {
  items: Job[]
  total: number
  next_offset: number | null
  facets: { source: Record<string, number>; family: Record<string, number>; location: Record<string, number> }
}

export interface Stats {
  new_24h: number
  applied_24h: number
  follow_ups_due: number
  open_in_feed: number
  boards_active: number
  companies: number
  jobs_seen_total: number
  last_ingest_at: string | null
}

export interface SavedSearch {
  id: string
  site: string
  label: string
  url: string
  position: number
}

export interface CompanyRow {
  id: string
  name: string
  website: string | null
  description: string | null
  funding_stage: string | null
  size: string | null
  hq: string | null
  career_url: string | null
  in_yc_directory: boolean
  board: {
    ats: string
    token: string
    active: boolean
    discovery_method: string
    last_fetched_at: string | null
    last_error: string | null
  }
  open_fresher_jobs: number
}

export interface AgentRun {
  id: string
  worker: string
  task_key: string | null
  status: "running" | "success" | "error" | "partial"
  started_at: string
  finished_at: string | null
  counts: Record<string, number> | null
  error: string | null
  retries: number
}

export interface JobFilters {
  q?: string
  source?: string[]
  family?: string[]
  location?: string[]
  remote?: boolean
  posted_within_hours?: number
  all_roles?: boolean
  sort?: "newest" | "relevance"
}
