"use client"

import {
  type InfiniteData,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query"

import { api } from "./api"
import type { JobFilters, JobLabel, JobPage, JobStatus, LabelSummary } from "./types"

export const keys = {
  jobs: (f: JobFilters) => ["jobs", f] as const,
  job: (id: string) => ["job", id] as const,
  stats: ["stats"] as const,
  saved: ["saved-searches"] as const,
  companies: ["companies"] as const,
  runs: ["agent-runs"] as const,
  settings: ["settings"] as const,
  labels: ["label-summary"] as const,
}

export function useJobs(filters: JobFilters) {
  return useInfiniteQuery({
    queryKey: keys.jobs(filters),
    queryFn: ({ pageParam }) => api.jobs(filters, pageParam),
    initialPageParam: 0,
    getNextPageParam: (last) => last.next_offset ?? undefined,
    placeholderData: (prev) => prev,
    refetchInterval: 5 * 60_000,
  })
}

export function useJob(id: string | null) {
  return useQuery({ queryKey: keys.job(id ?? ""), queryFn: () => api.job(id!), enabled: Boolean(id) })
}

export function useStats() {
  return useQuery({ queryKey: keys.stats, queryFn: api.stats, refetchInterval: 60_000 })
}

/**
 * Status change with an optimistic update: the card leaves the feed immediately and comes back
 * if the server rejects the change.
 */
export function useSetStatus() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, status }: { id: string; status: JobStatus }) => api.setStatus(id, status),
    onMutate: async ({ id, status }) => {
      await qc.cancelQueries({ queryKey: ["jobs"] })
      const snapshots = qc.getQueriesData<InfiniteData<JobPage>>({ queryKey: ["jobs"] })
      if (status !== "discovered") {
        for (const [key, data] of snapshots) {
          if (!data) continue
          qc.setQueryData<InfiniteData<JobPage>>(key, {
            ...data,
            pages: data.pages.map((p) => {
              const items = p.items.filter((j) => j.id !== id)
              return { ...p, items, total: p.total - (p.items.length - items.length) }
            }),
          })
        }
      }
      return { snapshots }
    },
    onError: (_err, _vars, ctx) => {
      ctx?.snapshots.forEach(([key, data]) => qc.setQueryData(key, data))
    },
    onSettled: (_data, _err, { id }) => {
      qc.invalidateQueries({ queryKey: ["jobs"] })
      qc.invalidateQueries({ queryKey: keys.job(id) })
      qc.invalidateQueries({ queryKey: keys.stats })
    },
  })
}

export function useCorrectExperience() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, entry }: { id: string; entry: boolean }) => api.correctExperience(id, entry),
    onSuccess: (job) => {
      qc.setQueryData(keys.job(job.id), job)
      qc.invalidateQueries({ queryKey: ["jobs"] })
    },
  })
}


export function useLabelSummary() {
  return useQuery({ queryKey: keys.labels, queryFn: api.labelSummary })
}

/** Label a job (apply / maybe / no, or null to clear), updating cards immediately. */
export function useSetLabel() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, label }: { id: string; label: JobLabel | null }) => api.setLabel(id, label),
    onMutate: async ({ id, label }) => {
      await qc.cancelQueries({ queryKey: ["jobs"] })
      const snapshots = qc.getQueriesData<InfiniteData<JobPage>>({ queryKey: ["jobs"] })
      for (const [key, data] of snapshots) {
        if (!data) continue
        qc.setQueryData<InfiniteData<JobPage>>(key, {
          ...data,
          pages: data.pages.map((p) => ({ ...p, items: p.items.map((j) => (j.id === id ? { ...j, my_label: label } : j)) })),
        })
      }
      return { snapshots }
    },
    onError: (_e, _v, ctx) => ctx?.snapshots.forEach(([key, data]) => qc.setQueryData(key, data)),
    onSuccess: (summary: LabelSummary) => qc.setQueryData(keys.labels, summary),
    onSettled: (_d, _e, { id }) => qc.invalidateQueries({ queryKey: keys.job(id) }),
  })
}
