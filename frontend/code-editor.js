/* CodeMirror is initialized by the shared HTML editor module in Phase 3. */
import { EditorState } from "@codemirror/state";
import { EditorView } from "@codemirror/view";
import { defaultKeymap } from "@codemirror/commands";
import { html } from "@codemirror/lang-html";
import { defaultHighlightStyle, syntaxHighlighting } from "@codemirror/language";
import { searchKeymap } from "@codemirror/search";

window.CMSCodeEditor = {
  EditorState,
  EditorView,
  defaultKeymap,
  html,
  searchKeymap,
  syntaxHighlighting,
  defaultHighlightStyle,
};
