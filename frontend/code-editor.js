import { EditorState } from "@codemirror/state";
import {
  EditorView,
  keymap,
  lineNumbers,
  highlightActiveLine,
  highlightActiveLineGutter,
  drawSelection,
} from "@codemirror/view";
import { defaultKeymap, history, historyKeymap, indentWithTab } from "@codemirror/commands";
import { html } from "@codemirror/lang-html";
import {
  bracketMatching,
  defaultHighlightStyle,
  indentOnInput,
  syntaxHighlighting,
} from "@codemirror/language";
import { highlightSelectionMatches, searchKeymap } from "@codemirror/search";

const instances = new WeakMap();

function mount(textarea) {
  if (!textarea || instances.has(textarea)) return instances.get(textarea);
  let syncing = false;
  const host = document.createElement("div");
  host.className = "cms-code-editor";
  textarea.insertAdjacentElement("afterend", host);
  textarea.classList.add("cms-code-editor-source");

  const view = new EditorView({
    parent: host,
    state: EditorState.create({
      doc: textarea.value,
      extensions: [
        lineNumbers(),
        highlightActiveLineGutter(),
        history(),
        drawSelection(),
        indentOnInput(),
        bracketMatching(),
        syntaxHighlighting(defaultHighlightStyle, { fallback: true }),
        highlightActiveLine(),
        highlightSelectionMatches(),
        keymap.of([...defaultKeymap, ...historyKeymap, ...searchKeymap, indentWithTab]),
        html(),
        EditorView.lineWrapping,
        EditorView.contentAttributes.of({
          "aria-label": textarea.getAttribute("aria-label") || "HTML source code",
          spellcheck: "false",
        }),
        EditorView.updateListener.of((update) => {
          if (!update.docChanged) return;
          syncing = true;
          textarea.value = update.state.doc.toString();
          textarea.dispatchEvent(new Event("input", { bubbles: true }));
          syncing = false;
        }),
        EditorView.theme({
          "&": { minHeight: "26rem", fontSize: "13px" },
          ".cm-scroller": { fontFamily: "var(--font-mono, ui-monospace, monospace)", lineHeight: "1.55" },
          ".cm-content": { padding: "12px 0" },
          ".cm-gutters": { backgroundColor: "var(--color-surface-2, #f8fafc)", borderRight: "1px solid var(--color-border, #e5e7eb)" },
          "&.cm-focused": { outline: "3px solid color-mix(in srgb, var(--color-blue, #2563eb) 18%, transparent)" },
        }),
      ],
    }),
  });

  textarea.addEventListener("input", () => {
    if (syncing) return;
    const next = textarea.value;
    if (next === view.state.doc.toString()) return;
    view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: next } });
  });
  instances.set(textarea, view);
  return view;
}

function mountAll(root = document) {
  root.querySelectorAll("textarea[data-code-editor]").forEach(mount);
}

window.CMSCodeEditor = { mount, mountAll, instances };
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => mountAll());
else mountAll();
