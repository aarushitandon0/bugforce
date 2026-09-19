"use client";

import { FlaskConical, Files, Info, Waypoints } from "lucide-react";
import type { ComponentType } from "react";

export type PanelId = "files" | "trace" | "tests" | "brief";

const PANELS: { id: PanelId; label: string; Icon: ComponentType<{ size?: number; strokeWidth?: number }> }[] = [
  { id: "trace", label: "traceback", Icon: Waypoints },
  { id: "files", label: "files", Icon: Files },
  { id: "tests", label: "failing tests", Icon: FlaskConical },
  { id: "brief", label: "the brief", Icon: Info },
];

/**
 * The 48px icon rail. Clicking an icon swaps what the side panel shows;
 * clicking the active one collapses the panel, the way an editor does.
 */
export function ActivityBar({
  active,
  onSelect,
  badges,
}: {
  active: PanelId | null;
  onSelect: (panel: PanelId) => void;
  /** small count on an icon, e.g. how many tests are red */
  badges?: Partial<Record<PanelId, number>>;
}) {
  return (
    <nav
      aria-label="panels"
      className="flex w-12 shrink-0 flex-row items-center gap-1 border-b border-line bg-surface-2 px-1 lg:h-full lg:flex-col lg:items-stretch lg:border-r lg:border-b-0 lg:px-0 lg:py-1"
    >
      {PANELS.map(({ id, label, Icon }) => {
        const isActive = active === id;
        const badge = badges?.[id];
        return (
          <button
            key={id}
            type="button"
            onClick={() => onSelect(id)}
            title={label}
            aria-label={label}
            aria-pressed={isActive}
            className={`relative flex h-11 w-12 shrink-0 items-center justify-center border-l-2 outline-none transition-colors duration-[120ms] hover:text-text focus-visible:text-text ${
              isActive ? "border-frame bg-surface-2 text-text" : "border-transparent text-muted"
            }`}
          >
            <Icon size={18} strokeWidth={1.5} />
            {badge !== undefined && badge > 0 && (
              <span className="absolute right-[7px] bottom-[6px] text-[9px] leading-none tabular-nums text-gap">
                {badge > 99 ? "99+" : badge}
              </span>
            )}
          </button>
        );
      })}
    </nav>
  );
}
