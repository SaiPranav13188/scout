"use client"

import { createClient, type SupabaseClient } from "@supabase/supabase-js"

let client: SupabaseClient | null = null

/** Local development only (never in production builds): see docs/SETUP.md "Run locally". */
export function devAuthToken(): string | null {
  if (process.env.NODE_ENV === "production") return null
  return process.env.NEXT_PUBLIC_DEV_AUTH_TOKEN || null
}

export function supabaseConfigured() {
  return Boolean(process.env.NEXT_PUBLIC_SUPABASE_URL && process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY)
}

export function getSupabase(): SupabaseClient {
  if (!client) {
    const url = process.env.NEXT_PUBLIC_SUPABASE_URL
    const key = process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY
    if (!url || !key) {
      throw new Error("Supabase is not configured. Set NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY (see docs/SETUP.md).")
    }
    client = createClient(url, key, { auth: { persistSession: true, autoRefreshToken: true } })
  }
  return client
}
