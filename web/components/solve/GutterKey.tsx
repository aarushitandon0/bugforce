/**
 * What the two marks in the editor gutter mean.
 *
 * It lived at the foot of the right rail, about 900px from the thing it
 * explains, so it rendered as prose nobody connected to a marker. It is now a
 * real key -- the same 7px glyphs the gutter draws -- and it appears twice:
 * inline at the top of the editor, where the marks are, and once more in the
 * rail for anyone reading the rail top to bottom.
 *
 * Shape carries the distinction and colour only reinforces it: filled square
 * is the frame that raised, hollow square is a frame above it. Every marker
 * also carries the same wording in a title attribute (see lib/editor.ts).
 */
export function GutterKey({ className = "", inline = false }: { className?: string; inline?: boolean }) {
  return (
    <dl className={`flex flex-wrap items-center gap-x-4 gap-y-1 t-small text-muted ${className}`}>
      <span className="flex items-center gap-2">
        <dt aria-hidden className="block h-[7px] w-[7px] shrink-0 border border-raise bg-raise" />
        <dd>{inline ? "raised" : "raised here"}</dd>
      </span>
      <span className="flex items-center gap-2">
        <dt aria-hidden className="box-border block h-[7px] w-[7px] shrink-0 border border-frame" />
        <dd>{inline ? "frame above" : "a frame above it"}</dd>
      </span>
    </dl>
  );
}
