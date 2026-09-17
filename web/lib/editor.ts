"use client";

/**
 * CodeMirror 6 for the solve screen.
 *
 * One EditorView is reused across tabs; each file keeps its own EditorState
 * (so undo history survives switching) and scroll snapshot. Trace frames are
 * a per-state field of line markers that is mapped through edits, so a gutter
 * marker stays on the right line after lines are inserted above it.
 */

import { defaultKeymap, history, historyKeymap, indentWithTab } from "@codemirror/commands";
import { python } from "@codemirror/lang-python";
import { bracketMatching, HighlightStyle, indentOnInput, indentUnit, syntaxHighlighting } from "@codemirror/language";
import { highlightSelectionMatches, search, searchKeymap } from "@codemirror/search";
import { EditorState, RangeSet, StateEffect, StateField, type Extension } from "@codemirror/state";
import {
  Decoration,
  drawSelection,
  EditorView,
  gutter,
  GutterMarker,
  highlightActiveLine,
  highlightActiveLineGutter,
  keymap,
  lineNumbers,
  type DecorationSet,
} from "@codemirror/view";
import { tags as t } from "@lezer/highlight";

const C = {
  base: "#0A0B0D",
  panel: "#111316",
  line: "#1E2126",
  text: "#C9CDD3",
  dim: "#6B7280",
  error: "#E8A33D",
  success: "#4ADE80",
  causal: "#8B5CF6",
};

// ---------------------------------------------------------------------------
// look
// ---------------------------------------------------------------------------

const theme = EditorView.theme(
  {
    "&": { height: "100%", backgroundColor: C.base, color: C.text, fontSize: "13px" },
    "&.cm-focused": { outline: "none" },
    ".cm-scroller": { fontFamily: "inherit", lineHeight: "1.65" },
    ".cm-content": { caretColor: C.text, padding: "8px 0" },
    ".cm-line": { padding: "0 16px 0 12px" },
    // block cursor, blinking on CodeMirror's own timer
    ".cm-cursor, .cm-dropCursor": { borderLeft: "none", width: "0.6em", backgroundColor: `${C.text}B3` },
    ".cm-cursorLayer": { mixBlendMode: "normal" },
    "&.cm-focused > .cm-scroller > .cm-selectionLayer .cm-selectionBackground, .cm-selectionBackground, ::selection":
      { backgroundColor: `${C.causal}4D` },
    ".cm-activeLine": { backgroundColor: `${C.panel}` },
    ".cm-gutters": { backgroundColor: C.base, color: C.dim, border: "none", borderRight: `1px solid ${C.line}` },
    ".cm-lineNumbers .cm-gutterElement": { padding: "0 10px 0 6px", minWidth: "4ch" },
    ".cm-activeLineGutter": { backgroundColor: C.panel, color: C.text },
    ".cm-frames": { width: "18px" },
    ".cm-frames .cm-gutterElement": { display: "flex", alignItems: "center", justifyContent: "center" },
    ".cm-frame-mark": { display: "block", width: "7px", height: "7px" },
    ".cm-frame-mark.is-frame": { border: `1px solid ${C.causal}` },
    ".cm-frame-mark.is-visited": { backgroundColor: C.causal },
    ".cm-frame-mark.is-exception": { backgroundColor: C.error, border: `1px solid ${C.error}` },
    ".cm-frame-line": { backgroundColor: `${C.causal}14` },
    ".cm-exception-line": { backgroundColor: `${C.error}24` },
    ".cm-target-line": { outline: `1px solid ${C.causal}66`, outlineOffset: "-1px" },
    ".cm-matchingBracket, &.cm-focused .cm-matchingBracket": {
      backgroundColor: "transparent",
      outline: `1px solid ${C.dim}`,
    },
    ".cm-nonmatchingBracket": { color: C.error },
    ".cm-selectionMatch": { backgroundColor: `${C.dim}33` },
    ".cm-searchMatch": { backgroundColor: `${C.error}33`, outline: `1px solid ${C.error}66` },
    ".cm-searchMatch.cm-searchMatch-selected": { backgroundColor: `${C.error}66` },
    ".cm-panels": { backgroundColor: C.panel, color: C.text, fontFamily: "inherit" },
    ".cm-panels.cm-panels-bottom": { borderTop: `1px solid ${C.line}` },
    ".cm-panels.cm-panels-top": { borderBottom: `1px solid ${C.line}` },
    ".cm-search": { fontSize: "12px", padding: "6px 10px" },
    ".cm-search input, .cm-search button, .cm-search label": { fontFamily: "inherit", fontSize: "12px" },
    ".cm-textfield": {
      backgroundColor: C.base,
      color: C.text,
      border: `1px solid ${C.line}`,
      borderRadius: "0",
      padding: "2px 6px",
    },
    ".cm-textfield:focus": { borderColor: C.dim, outline: "none" },
    ".cm-button": {
      backgroundImage: "none",
      backgroundColor: "transparent",
      color: C.text,
      border: `1px solid ${C.line}`,
      borderRadius: "0",
      padding: "2px 8px",
    },
    ".cm-button:hover": { borderColor: C.dim },
    ".cm-panel.cm-search [name=close]": { color: C.dim, fontSize: "16px" },
    ".cm-tooltip": { backgroundColor: C.panel, border: `1px solid ${C.line}`, color: C.text },
  },
  { dark: true },
);

/*
 * Monochrome on purpose: the only colour in the editor is meaning (amber for
 * where it failed, violet for the trace), so syntax gets weight and dimness,
 * not hues.
 */
const highlight = HighlightStyle.define([
  { tag: [t.keyword, t.controlKeyword, t.operatorKeyword, t.definitionKeyword, t.moduleKeyword], fontWeight: "700" },
  { tag: [t.bool, t.null, t.self], fontWeight: "700" },
  { tag: [t.comment, t.lineComment, t.blockComment, t.docString], color: C.dim },
  { tag: [t.string, t.special(t.string)], color: `color-mix(in srgb, ${C.text} 62%, ${C.dim})` },
  { tag: [t.number], color: C.text },
  { tag: [t.function(t.definition(t.variableName)), t.definition(t.className)], color: C.text, fontWeight: "700" },
  { tag: [t.meta, t.annotation], color: C.dim },
  { tag: t.invalid, color: C.error },
]);

// ---------------------------------------------------------------------------
// trace frames
// ---------------------------------------------------------------------------

export interface FrameMark {
  /** 1-based */
  line: number;
  exception: boolean;
  /** frame indices on this line */
  frames: number[];
  title: string;
}

class FrameMarker extends GutterMarker {
  constructor(
    readonly exception: boolean,
    readonly visited: boolean,
    readonly title: string,
  ) {
    super();
  }
  eq(other: FrameMarker) {
    return other.exception === this.exception && other.visited === this.visited && other.title === this.title;
  }
  toDOM() {
    const el = document.createElement("span");
    el.className = `cm-frame-mark ${this.exception ? "is-exception" : "is-frame"}${this.visited ? " is-visited" : ""}`;
    el.title = this.title;
    return el;
  }
}

interface FrameState {
  marks: { pos: number; mark: FrameMark }[];
  visited: ReadonlySet<number>;
  lines: DecorationSet;
  target: DecorationSet;
}

/** Point the "you are here" rail at a document position (or clear it). */
export const setTarget = StateEffect.define<number | null>();
/** Which frame indices have been visited, for filled markers. */
export const setVisited = StateEffect.define<ReadonlySet<number>>();

function lineDecorations(marks: FrameState["marks"]): DecorationSet {
  return Decoration.set(
    marks.map(({ pos, mark }) =>
      Decoration.line({ class: mark.exception ? "cm-exception-line" : "cm-frame-line" }).range(pos),
    ),
    true,
  );
}

function frameField(marks: FrameMark[], visited: ReadonlySet<number>) {
  return StateField.define<FrameState>({
    create(state) {
      const placed = marks
        .filter((m) => m.line >= 1 && m.line <= state.doc.lines)
        .map((mark) => ({ pos: state.doc.line(mark.line).from, mark }))
        .sort((a, b) => a.pos - b.pos);
      return { marks: placed, visited, lines: lineDecorations(placed), target: Decoration.none };
    },
    update(value, tr) {
      let next = value;
      if (tr.docChanged) {
        const marks = value.marks.map(({ pos, mark }) => ({
          pos: tr.state.doc.lineAt(tr.changes.mapPos(pos)).from,
          mark,
        }));
        next = { ...value, marks, lines: lineDecorations(marks), target: value.target.map(tr.changes) };
      }
      for (const effect of tr.effects) {
        if (effect.is(setVisited)) next = { ...next, visited: effect.value };
        if (effect.is(setTarget)) {
          next = {
            ...next,
            target:
              effect.value === null
                ? Decoration.none
                : Decoration.set([Decoration.line({ class: "cm-target-line" }).range(tr.state.doc.lineAt(effect.value).from)]),
          };
        }
      }
      return next;
    },
    provide: (field) => [
      EditorView.decorations.from(field, (v) => v.lines),
      EditorView.decorations.from(field, (v) => v.target),
      gutter({
        class: "cm-frames",
        markers: (view) => {
          const { marks, visited } = view.state.field(field);
          return RangeSet.of(
            marks.map(({ pos, mark }) =>
              new FrameMarker(mark.exception, mark.frames.some((f) => visited.has(f)), mark.title).range(pos),
            ),
            true,
          );
        },
      }),
    ],
  });
}

// ---------------------------------------------------------------------------
// workspace
// ---------------------------------------------------------------------------

export interface WorkspaceFile {
  path: string;
  /** editor text: "\n" line endings */
  text: string;
  readOnly: boolean;
  marks: FrameMark[];
}

export class Workspace {
  readonly view: EditorView;
  private states = new Map<string, EditorState>();
  private scroll = new Map<string, StateEffect<unknown>>();
  private active: string | null = null;
  private visited: ReadonlySet<number> = new Set();

  constructor(
    parent: HTMLElement,
    private readonly onDocChange: (path: string) => void,
  ) {
    this.view = new EditorView({
      parent,
      dispatchTransactions: (trs, view) => {
        view.update(trs);
        if (this.active) {
          this.states.set(this.active, view.state);
          if (trs.some((tr) => tr.docChanged)) this.onDocChange(this.active);
        }
      },
    });
  }

  has(path: string): boolean {
    return this.states.has(path);
  }

  private create(file: WorkspaceFile): EditorState {
    const extensions: Extension[] = [
      lineNumbers(),
      highlightActiveLineGutter(),
      highlightActiveLine(),
      history(),
      drawSelection({ cursorBlinkRate: 1060 }),
      EditorState.allowMultipleSelections.of(true),
      indentOnInput(),
      indentUnit.of("    "),
      bracketMatching(),
      highlightSelectionMatches(),
      search({ top: false }),
      keymap.of([...searchKeymap, ...historyKeymap, indentWithTab, ...defaultKeymap]),
      syntaxHighlighting(highlight),
      theme,
      frameField(file.marks, this.visited),
      EditorState.tabSize.of(4),
    ];
    if (file.path.endsWith(".py")) extensions.push(python());
    if (file.readOnly) extensions.push(EditorState.readOnly.of(true));
    return EditorState.create({ doc: file.text, extensions });
  }

  /** Shows a file, creating its state on first open, optionally jumping to a line. */
  show(file: WorkspaceFile, line?: number): void {
    if (this.active && this.active !== file.path) {
      this.scroll.set(this.active, this.view.scrollSnapshot());
    }
    let state = this.states.get(file.path);
    if (!state) {
      state = this.create(file);
      this.states.set(file.path, state);
    }
    if (this.active !== file.path) {
      this.active = file.path;
      this.view.setState(state);
      this.view.dispatch({ effects: setVisited.of(this.visited) });
      const snapshot = this.scroll.get(file.path);
      if (snapshot && line === undefined) this.view.dispatch({ effects: snapshot });
    }
    if (line !== undefined) this.jump(line);
  }

  jump(line: number): void {
    const doc = this.view.state.doc;
    const target = doc.line(Math.max(1, Math.min(doc.lines, line)));
    const indent = /^\s*/.exec(target.text)![0].length;
    this.view.dispatch({
      selection: { anchor: target.from + indent },
      effects: [setTarget.of(target.from), EditorView.scrollIntoView(target.from, { y: "center" })],
    });
  }

  setVisited(visited: ReadonlySet<number>): void {
    this.visited = visited;
    if (this.active) this.view.dispatch({ effects: setVisited.of(visited) });
    // inactive states pick it up when they are next shown
  }

  text(path: string): string | null {
    return this.states.get(path)?.doc.toString() ?? null;
  }

  /** Replaces a file's contents in place, as one undoable change. */
  replace(path: string, text: string): void {
    if (this.active === path) {
      this.view.dispatch({ changes: { from: 0, to: this.view.state.doc.length, insert: text } });
      return;
    }
    const state = this.states.get(path);
    if (state) this.states.set(path, state.update({ changes: { from: 0, to: state.doc.length, insert: text } }).state);
  }

  focus(): void {
    this.view.focus();
  }

  destroy(): void {
    this.view.destroy();
  }
}
