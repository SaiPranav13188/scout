import { cn } from "@/lib/utils"

/** Slow-moving colour field behind hero surfaces. Pure CSS; pauses under reduced motion. */
export function Aurora({ className }: { className?: string }) {
  return (
    <div aria-hidden className={cn("pointer-events-none absolute inset-0 overflow-hidden", className)}>
      <div
        className="absolute -top-1/3 left-1/2 h-[70vh] w-[90vw] -translate-x-1/2 animate-aurora rounded-full opacity-60 blur-[110px]"
        style={{ background: "radial-gradient(closest-side, color-mix(in oklch, var(--brand) 55%, transparent), transparent)" }}
      />
      <div
        className="absolute top-1/4 -left-1/4 h-[55vh] w-[55vw] animate-aurora rounded-full opacity-40 blur-[120px] [animation-delay:-6s]"
        style={{ background: "radial-gradient(closest-side, color-mix(in oklch, var(--info) 50%, transparent), transparent)" }}
      />
      <div
        className="absolute -right-1/4 bottom-0 h-[50vh] w-[50vw] animate-aurora rounded-full opacity-30 blur-[120px] [animation-delay:-12s]"
        style={{ background: "radial-gradient(closest-side, color-mix(in oklch, var(--success) 45%, transparent), transparent)" }}
      />
      <div
        className="absolute inset-0 opacity-[0.15]"
        style={{
          backgroundImage:
            "linear-gradient(to right, oklch(1 0 0 / 6%) 1px, transparent 1px), linear-gradient(to bottom, oklch(1 0 0 / 6%) 1px, transparent 1px)",
          backgroundSize: "56px 56px",
          maskImage: "radial-gradient(ellipse at center, black 20%, transparent 70%)",
        }}
      />
    </div>
  )
}
