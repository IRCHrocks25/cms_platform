# Sticky Source Save Action Design

## Goal

Keep the primary save action reachable on the page HTML and template source editors without covering the editor or separating the action from the object it affects.

## Approved design

Both source-editing screens use one action bar in the page header. The title and actions form the leading row, with the subtitle supporting the title. The form remains in the content card; it receives a stable `id`, and the header submit button associates with it through HTML's `form` attribute. The page editor retains Cancel, and the template editor gains the equivalent Cancel link back to the template list. No bottom save action remains.

On desktop and intermediate widths, the scoped source-editor header is sticky at the top of the document scroll container. It uses the opaque canvas token and a single hairline bottom border for separation. The global `.page-head` remains unchanged. At narrow widths, the existing 60px mobile application bar stays uppermost; only the title/action row sticks below it while the subtitle scrolls away. The row wraps intrinsically so actions move below a long title instead of compressing it, and its controls have 44px mobile targets.

The annotation status returns to normal flow because a second sticky element would collide with the variable-height header. Existing comparison-overlay error and recovery controls are unchanged.

Each source form marks its CodeMirror-backed textarea as the dirty-state source. The loaded textarea value is the baseline. Input events—already emitted by the CodeMirror synchronization path—show a muted “Unsaved changes” status when the current source differs and hide it when it matches again. A valid submit clears the status immediately. The canvas background keeps the existing muted token above 4.5:1 contrast.

A document-level capture handler recognizes only unmodified Cmd/Ctrl+S on these marked source-editor screens, prevents the browser save dialog, and calls `requestSubmit()` on the associated form. It is a no-op when no marked form exists and leaves every other CodeMirror shortcut alone. Native button and implicit Enter submission continue through the same form.

## Alternatives considered

- Wrap the page header in the form. Rejected because it restructures the page and needlessly couples the breadcrumb/header to form layout.
- Keep or duplicate a bottom submit. Rejected because two primary actions weaken hierarchy and the bottom control still covers editor content.
- Offset the annotation status below the sticky header. Rejected because the header height changes when titles, subtitles, and actions wrap.
- Keep the full mobile header sticky. Rejected because the long page-editor subtitle would consume an unreasonable portion of a 390px viewport.

## Verification and release

Render tests cover the external `form=` association, header placement, dirty-state hook, and removal of the bottom action bar. Focused tests establish red/green behavior before implementation. After the full Django suite and asset parity gate pass, the change is reviewed and merged. Staging verification captures both screens at 1280px and 390px in one batch after seeding a large document and scrolling deeply. The same audit proves the sticky action remains visible, both button and Cmd/Ctrl+S submit paths work, template implicit Enter submission works, and the dirty indicator appears and clears. One consolidated correction round is allowed. Production follows only after staging passes, with the prior production revision and rollback action recorded first; production verification is limited to health and one read-only client render.
