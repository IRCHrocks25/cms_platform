# Brief: icons on action items, matching the pack already in use

Same branch, `feat/cms-ui-followups`, on top of `41d196b`.

Menu items and action buttons are text-only, so scanning a menu means reading every label.
Add icons that say what each action does.

## The pack is already Lucide — match it, don't add a second one

The sidebar icons in `templates/base.html` are Lucide paths, hand-inlined as SVG. `Editor` is
`pen-line`, `Preview site` is `external-link`, and so on. The convention:

```html
<svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor"
     stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
```

`.nav-icon` is `18×18` (`frontend/dashboard.css:545`).

**Keep inlining Lucide paths. Do not add an icon dependency, a sprite build step, or a runtime
icon library.** `package.json` already carries five dependencies added by the overhaul; this
does not need a sixth. Copy the paths from lucide.dev.

Add a `.menu-icon` class alongside `.nav-icon` for the smaller in-menu size (16px reads better
than 18px at menu density — check it and use your judgement). Every icon is decorative:
`aria-hidden="true"`, with the text label remaining the accessible name. An icon must never be
the only thing identifying an action.

## Where

Everywhere an action appears as a menu item or a labelled button, in the templates the previous
two briefs touched: `tenant_detail.html`, `page_list.html`, `tenant_list.html`, `blog_list.html`,
`editor.html`, `blog_form.html`. Extend to other dashboard templates where the same action names
appear, so one action does not have an icon on one screen and not on another.

Suggested mapping — use your judgement where a better Lucide glyph exists, and say what you
changed and why:

| Action | Lucide |
|---|---|
| Edit content | `square-pen` (or `pen-line`, matching the sidebar's Editor entry) |
| Edit HTML source | `file-code` |
| View / View site | `external-link` |
| Details | `arrow-right` or `info` |
| Publish | `upload` |
| Preview | `eye` |
| Settings | `settings` |
| History / versions | `history` |
| Gallery / media | `image` |
| Team / members | `users` |
| Add member | `user-plus` |
| Pages | `file-text` (matches the sidebar Pages entry) |
| Blog | `newspaper` or the sidebar's existing blog glyph |
| Duplicate | `copy` |
| Remove / Delete | `trash-2` |

**Consistency rule:** if an action already has an icon in the sidebar, reuse that exact glyph.
`Pages` in a menu and `Pages` in the sidebar must not be two different pictures.

## Fix the trigger while you are there

The three-dot trigger is `viewBox="0 0 18 18"` with three filled `<circle>` elements — a filled
shape in a codebase whose icons are all 24×24 stroked outlines. It is the one icon that does not
match. Replace it with Lucide `ellipsis` (or `ellipsis-vertical` if that reads better in a table
row) using the standard convention above.

## Destructive actions

Keep `trash-2` visually distinct — the danger colour already in the token set, not a new hue.
Do not let an icon alone imply destructiveness; the label and any existing confirmation stay.

## Verification

- `python manage.py test` — 808 before this. Report the new number.
- `npm run check:assets` must pass.
- `docker compose -f docker-compose.yml build` must still succeed.
- Screenshots at 375 / 768 / 1280 into `docs/ui-audit/`: a row menu open with icons, the editor
  top-bar menu open, and the sites list showing the new trigger.
- Confirm icons do not shift menu item heights or cause text to wrap at 375px.
- Every icon carries `aria-hidden="true"` and no action is identified by icon alone — grep for
  it rather than assuming.
- Push so staging redeploys, then check it there.

## Reporting

```bash
/home/bernardjr/.claude/skills/orchestrating-herdr-agents/scripts/herdr-say.sh \
  --state done --task CMS-UI-4 --why "<one concrete sentence>"
```

Do not open a pull request.
