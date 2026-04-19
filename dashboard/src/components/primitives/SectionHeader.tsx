/**
 * Section header — label text followed by a 1px border line filling
 * the remaining horizontal space. Used above every scrolled section.
 */
export function SectionHeader({ label }: { label: string }) {
  return (
    <div className="mb-6 flex items-center gap-3">
      <span className="font-mono text-[11px] uppercase tracking-[0.18em] text-text-40">
        {label}
      </span>
      <span className="h-px flex-1 bg-border" />
    </div>
  );
}
