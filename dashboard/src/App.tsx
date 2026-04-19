import { useState } from "react";
import { TrackRecord } from "@/views/TrackRecord";
import { SignalArchive } from "@/views/SignalArchive";
import { cn } from "@/lib/cn";

type View = "track" | "archive";

export default function App() {
  const [view, setView] = useState<View>("track");
  return (
    <>
      <Nav view={view} setView={setView} />
      {view === "track" ? <TrackRecord /> : <SignalArchive />}
    </>
  );
}

function Nav({ view, setView }: { view: View; setView: (v: View) => void }) {
  return (
    <nav className="fixed top-6 left-1/2 z-20 -translate-x-1/2">
      <div className="flex items-center gap-1 rounded-full border border-border bg-bg/85 px-1.5 py-1.5 backdrop-blur-md">
        <NavTab active={view === "track"} onClick={() => setView("track")}>
          Track Record
        </NavTab>
        <NavTab active={view === "archive"} onClick={() => setView("archive")}>
          Archive
        </NavTab>
      </div>
    </nav>
  );
}

function NavTab({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "rounded-full px-4 py-1.5 font-mono text-[10px] uppercase tracking-[0.14em] transition-colors duration-150",
        active
          ? "bg-surface-raised text-text"
          : "text-text-50 hover:text-text-70",
      )}
    >
      {children}
    </button>
  );
}
