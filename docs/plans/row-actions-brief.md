# Brief: row-level action menus, and selects that collapse inside tables

Same branch, `feat/cms-ui-followups`. Two defects the owner found by looking at the seeded
staging site — which is exactly what the seeder was for.

Neither is your fault. The previous brief scoped the overflow menu to audit Finding #9, which
names only `editor.html` and `blog_form.html`, and you implemented both correctly. The owner
was describing list and table rows, which were never in scope. This brief covers them.

---

## 1. Row actions are all displayed at once

**Where:** `templates/dashboard/tenant_list.html` (the Actions column) and
`templates/dashboard/tenant_detail.html` (the Pages card rows and the Members table).
Verified: both have zero `aria-haspopup` / `role="menu"` markup today.

Currently every row shows every action inline — sites show *View site / Edit / Details*, pages
show *Edit / HTML / View*, members show *Remove*. At the current column widths the sites table
wraps "View site" onto its own line with Edit and Details beneath it, and the wrap point differs
per row, so nothing aligns down the column.

Apply the same treatment Finding #9 asked for, one level down:

- **Keep one primary action visible per row.** Edit for a site, Edit for a page. Choose it
  deliberately and keep it consistent down the column.
- **Move the rest behind a row-level menu** — a three-dot trigger with a real accessible name
  (`aria-label="More actions for <site name>"` or similar; a bare glyph is not acceptable).
- **Reuse the menu primitive you already built** for the editor top bar. Do not write a second
  one. Same keyboard contract: `aria-haspopup`, `aria-expanded`, arrow-key movement, Escape to
  close, focus returned to the trigger.
- **Destructive actions** (Remove, Delete) go in the menu, visually separated, and keep whatever
  confirmation they have today.
- The Actions column must stop wrapping and must align down the column at 1280, 768 and 375.

Check the other list templates for the same pattern while you are here —
`template_list.html`, `user_list.html`, `blog_list.html`, `page_list.html`,
`custom_domain_list.html`. Apply the same treatment where a row has three or more actions; say
in your report which ones you changed and which you judged fine as-is.

## 2. Selects collapse to one character inside tables

**Where:** the Role select in the Members table, `tenant_detail.html:123`
(`class="select select-sm"`).

On staging it renders about one character wide — "E" for Editor, "C" for the owner role.

Cause: `frontend/dashboard.css:251` sets `.input, .textarea, .select { width: 100% }`.
`.data-table` (`:672`) sets no `table-layout`, so columns are auto-sized, and a `width: 100%`
select contributes no intrinsic minimum width. Once Username and Email claim the available
space the Role column is squeezed to nothing and the select goes with it.

This is the same class of bug as the integrations dropdown you just fixed, inverted: that one
had no maximum, this one has no minimum. **Fix it generally, not just for this one select** —
any form control inside a data table has the same exposure. A `min-width` floor on
table-embedded controls, or an explicit column-width strategy on `.data-table`, or both.

Then sweep every `<select>`, `<input>` and `<button>` that lives inside a `.data-table` cell
and confirm none of them collapse or overflow at 375 / 768 / 1280. Report what you found.

---

## Verification

- `python manage.py test` must pass — 806 before this change. Report the new number.
- `npm run check:assets` must pass.
- `docker compose -f docker-compose.yml build` must still succeed.
- Screenshots into `docs/ui-audit/` at 375 / 768 / 1280: the sites list with the row menu closed
  and open, the client site page showing Pages and Members, and the Members table with the Role
  select rendering properly.
- Keyboard-only pass over a row menu: reach it by Tab, open it, move with arrows, close with
  Escape, confirm focus returns to the trigger.
- Push so staging redeploys, then confirm on staging with the seeded data — that data is what
  exposed both of these, so a check against an empty database proves nothing.

## Reporting

```bash
/home/bernardjr/.claude/skills/orchestrating-herdr-agents/scripts/herdr-say.sh \
  --state done --task CMS-UI-3 --why "<one concrete sentence>"
```

Do not open a pull request.
