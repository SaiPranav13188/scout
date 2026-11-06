import { cn } from "@/lib/utils"

/** Scout mark: a radar sweep inside a rounded square. */
export function Logo({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={cn("size-7", className)} aria-hidden>
      <defs>
        <linearGradient id="scout-g" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="var(--brand)" />
          <stop offset="100%" stopColor="color-mix(in oklch, var(--brand) 40%, var(--info))" />
        </linearGradient>
      </defs>
      <rect x="1" y="1" width="30" height="30" rx="9" fill="url(#scout-g)" />
      <circle cx="16" cy="16" r="8.5" fill="none" stroke="white" strokeOpacity=".35" strokeWidth="1.5" />
      <circle cx="16" cy="16" r="4.5" fill="none" stroke="white" strokeOpacity=".55" strokeWidth="1.5" />
      <path d="M16 16 L24.5 16 A8.5 8.5 0 0 0 20.25 8.64 Z" fill="white" fillOpacity=".9" />
      <circle cx="16" cy="16" r="1.6" fill="white" />
    </svg>
  )
}
