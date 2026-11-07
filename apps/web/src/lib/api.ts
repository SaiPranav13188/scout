"use client"

import { devAuthToken, getSupabase } from "./supabase"
import type {
  AgentRun, ApplicationRow, HealthSummary, CompanyRow, Contact, FactCheck, JobDetail, JobFilters, JobLabel, JobPage, JobStatus, LabelSummary,
  ResumeData, ResumeOverview, ResumeVersion, SavedSearch, Stats, TailoredResume,
} from "./types"

const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message)
  }
}

async function authToken(): Promise<string> {
  const token = devAuthToken() ?? (await getSupabase().auth.getSession()).data.session?.access_token
  if (!token) throw new ApiError(401, "Not signed in")
  return token
}

/** Download a file from the API (needs the auth header, so a plain link won't do). */
export async function downloadFile(path: string, fallbackName: string) {
  const res = await fetch(`${BASE}${path}`, { headers: { Authorization: `Bearer ${await authToken()}` } })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new ApiError(res.status, body?.detail ?? res.statusText)
  }
  const name = res.headers.get("Content-Disposition")?.match(/filename="([^"]+)"/)?.[1] ?? fallbackName
  const url = URL.createObjectURL(await res.blob())
  const a = Object.assign(document.createElement("a"), { href: url, download: name })
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 10_000)
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = devAuthToken() ?? (await getSupabase().auth.getSession()).data.session?.access_token
  if (!token) throw new ApiError(401, "Not signed in")
  const isForm = init.body instanceof FormData  // the browser sets the multipart boundary itself
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      ...(isForm ? {} : { "Content-Type": "application/json" }),
      Authorization: `Bearer ${token}`,
      ...init.headers,
    },
  })
  if (res.status === 204) return undefined as T
  const body = await res.json().catch(() => ({}))
  if (!res.ok) throw new ApiError(res.status, body?.detail ?? res.statusText)
  return body as T
}

function query(params: Record<string, unknown>) {
  const sp = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "" || v === false) continue
    if (Array.isArray(v)) v.forEach((x) => sp.append(k, String(x)))
    else sp.set(k, String(v))
  }
  const s = sp.toString()
  return s ? `?${s}` : ""
}

export const api = {
  jobs: (f: JobFilters, offset = 0) => request<JobPage>(`/api/jobs${query({ ...f, offset, limit: 30 })}`),
  job: (id: string) => request<JobDetail>(`/api/jobs/${id}`),
  setStatus: (id: string, status: JobStatus) =>
    request<JobDetail>(`/api/jobs/${id}/status`, { method: "POST", body: JSON.stringify({ status }) }),
  correctExperience: (id: string, entry_level: boolean) =>
    request<JobDetail>(`/api/jobs/${id}/experience`, { method: "POST", body: JSON.stringify({ entry_level }) }),
  stats: () => request<Stats>("/api/stats"),
  setLabel: (id: string, label: JobLabel | null) =>
    request<LabelSummary>(`/api/jobs/${id}/label`, { method: "PUT", body: JSON.stringify({ label }) }),
  labelSummary: () => request<LabelSummary>("/api/labels/summary"),
  visit: () => request<{ previous_visit_at: string | null }>("/api/visit", { method: "POST" }),
  settings: () => request<Record<string, unknown>>("/api/settings"),
  putSetting: (key: string, value: unknown) =>
    request<unknown>(`/api/settings/${key}`, { method: "PUT", body: JSON.stringify(value) }),
  savedSearches: () => request<SavedSearch[]>("/api/saved-searches"),
  addSavedSearch: (b: { site: string; label: string; url: string }) =>
    request<SavedSearch>("/api/saved-searches", { method: "POST", body: JSON.stringify(b) }),
  updateSavedSearch: (id: string, b: { site?: string; label?: string; url?: string }) =>
    request<SavedSearch>(`/api/saved-searches/${id}`, { method: "PATCH", body: JSON.stringify(b) }),
  deleteSavedSearch: (id: string) => request<void>(`/api/saved-searches/${id}`, { method: "DELETE" }),
  companies: () => request<CompanyRow[]>("/api/companies"),
  addBoard: (b: { url: string; company_name: string; website?: string }) =>
    request<{ ats: string; token: string; jobs_listed: number }>("/api/companies/boards", {
      method: "POST", body: JSON.stringify(b),
    }),
  agentRuns: () => request<AgentRun[]>("/api/agent-runs"),
  resume: () => request<ResumeOverview>("/api/resume"),
  resumeVersion: (v: number) => request<ResumeVersion>(`/api/resume/versions/${v}`),
  checkResume: (data: ResumeData) =>
    request<FactCheck>("/api/resume/check", { method: "POST", body: JSON.stringify({ data }) }),
  saveResume: (base_version: number, data: ResumeData, contact: Contact) =>
    request<ResumeOverview>("/api/resume", { method: "PUT", body: JSON.stringify({ base_version, data, contact }) }),
  activateResume: (v: number) => request<ResumeOverview>(`/api/resume/versions/${v}/activate`, { method: "POST" }),
  tailoredForJob: (jobId: string) => request<TailoredResume[]>(`/api/jobs/${jobId}/resumes`),
  tailorForJob: (jobId: string) => request<TailoredResume>(`/api/jobs/${jobId}/resume`, { method: "POST" }),
  applications: () => request<ApplicationRow[]>("/api/applications"),
  updateApplication: (jobId: string, b: { status?: string; notes?: string; follow_up_at?: string; clear_follow_up?: boolean }) =>
    request<ApplicationRow>(`/api/applications/${jobId}`, { method: "PATCH", body: JSON.stringify(b) }),
  healthSummary: () => request<HealthSummary>("/api/health/summary"),
  drafts: (jobId: string) => request<{ ok: boolean; cover_note?: string; recruiter_message?: string; unsupported?: string[] }>(
    `/api/jobs/${jobId}/drafts`, { method: "POST" }),
  tailoredAll: () => request<TailoredResume[]>("/api/resumes"),
  uploadResume: (file: File) => {
    const body = new FormData()
    body.append("file", file)
    return request<ResumeOverview>("/api/resume/upload", { method: "POST", body })
  },
}
