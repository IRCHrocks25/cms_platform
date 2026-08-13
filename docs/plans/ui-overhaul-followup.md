# Follow-up brief: tenant pages nav, then the unused Impeccable passes

Continuation of `docs/plans/ui-overhaul-brief.md`, same branch, same worktree. Everything
in that brief still binds — the product constraints, the production hazards, the staging
setup, and the reporting protocol. Read this as an addendum, not a replacement.

Two pieces of work, in order. Do not start Part 2 until Part 1 is committed and pushed.

---

## Part 1 — The client shell has a dead-end feature

`dashboard/urls.py:123-129` defines a complete tenant-scoped inner-page CRUD:

```
pages/                          page_list_self
pages/new/                      page_create_self
pages/<pk>/edit/                page_editor_self
pages/<pk>/preview/             page_preview_self
pages/<pk>/save/                page_save_self
pages/<pk>/publish/             page_publish_self
pages/<pk>/delete/              page_delete_self
```

**Zero templates reference any of them.** Verified on this branch after your Phase 4:
`grep -rl page_list_self templates/` returns nothing. The tenant sidebar in
`templates/base.html` has exactly three entries — Editor (`:49`), Blog (`:53`), Preview
site (`:57`). A logged-in client has working page routes and no way to reach them short of
typing the URL.

Your Phase 4 consolidated pages into the **agency** view (`tenant_detail.html`). That was
what the original brief asked for and it was done correctly. This is the other half: the
client's own shell.

### What to build

- A **Pages** entry in the tenant sidebar, given the same treatment Blog already has:
  `nav_section` highlighting, an icon consistent with the set, correct active state.
- The tenant-facing page list, create, and edit screens brought onto the new design system
  — the same components, density, loading states, and dialog primitive you built in Phases
  2 and 3. Do not leave these screens on the old look; they are currently unstyled by
  anything you did because you never rendered them.
- Empty state for a tenant with no inner pages, matching whatever pattern you established
  elsewhere.
- Whatever `nav_section` plumbing the corresponding views need in `dashboard/views.py`.

### Constraints that bite here specifically

- **No section add/remove for clients** and **no raw HTML editing for clients** — both from
  `CLAUDE.md`. `page_editor_self` is the structured editor and is fine to expose. There is
  deliberately no `page_edit_html_self`; do not create one, and do not link clients to the
  agency's `page_edit_html`.
- Agency routes stay agency-only. Check the decorator on each `*_self` view is
  `tenant_member_required`, not `agency_operator_required`, before you link to it.
- Deep links to the existing agency route `sites/<pk>/pages/` must keep working.

Commit Part 1 on its own before moving on.

---

## Part 2 — The Impeccable references you did not use

You used `operate.md`, `craft-floor.md`, `audit.md`, and `critique.md`, plus the mechanical
detector. The skill lives at `/home/bernardjr/.claude/skills/impeccable/`.

**First, the setup step you skipped.** Run `node <skill-base>/scripts/context.mjs` from the
repo root, once, and follow its directives. There is no `PRODUCT.md`, no `DESIGN.md`, and no
`.agents/` in this repo, which is why your own audit's implementation-integrity verdict is
still only partly resolved: 297 inline `style` attributes became 192, not zero, and there is
no recorded system for the next contributor to follow.

Then run these six, **in this order**. Each is a real pass with a deliverable, not a
checkbox.

| # | Reference | What it must actually produce here |
|---|---|---|
| 1 | `extract.md` | Pull the remaining **192 inline `style` attributes** and the hard-coded colors into tokens and reusable components. This is the one that closes your own "Fail" verdict. Report the before/after count. |
| 2 | `clarify.md` | UX copy, labels, and error messages. Your audit flagged status messaging as incomplete — the autosave state machine, publish results, domain verification, and every form error are in scope. |
| 3 | `harden.md` | Error states, edge cases, i18n readiness. Include the failure paths the audit named: offline, auth expiry, validation, server error. |
| 4 | `onboard.md` | First-run and empty states. Two real scenarios: a brand-new tenant with no content, and a fresh agency install with no templates and no sites. The staging database is empty, so you can exercise both there directly. |
| 5 | `polish.md` | Final quality pass across everything. |
| 6 | `document.md` | Write `DESIGN.md` **last**, so it describes what actually shipped rather than what was planned. If `context.mjs` or `document.md` wants `PRODUCT.md` first, run `init.md` to produce it. |

### Deliberately excluded

`bolder`, `quieter`, `delight`, `overdrive`, `colorize`, `typeset`. This is an **Operate**
surface — the user is completing a task, and `operate.md` is explicit that scanability,
consistency and native expectations outrank expression. Running the expressive commands on
an agency CMS dashboard would work against the audit you already wrote. If the owner later
asks for them specifically, that is a separate decision. Say so in your report rather than
running them quietly.

`live` is excluded too — it needs an interactive browser session with a human picking
elements, which does not apply to an unattended run.

`adapt` and `optimize` you effectively covered in Phase 3; skip unless your own re-read says
otherwise, and say which you concluded.

---

## Verification — unchanged and non-negotiable

- `python manage.py test` must stay green. It was 795 passing at your last report; report the
  actual number again, and any test you changed with the reason.
- **Build the production compose** — `docker compose -f docker-compose.yml build` — and
  confirm it still succeeds. The `Dockerfile` is shared with production; nobody has verified
  this since you added the Node stage, and it is the one change in this branch that can break
  a deploy for reasons unrelated to UI.
- `npm run check:assets` must pass, so committed output matches source.
- Refresh the before/after screenshots in `docs/ui-audit/` for anything you changed, and add
  the client-shell Pages screens at 375 / 768 / 1280.
- Keyboard-only pass over the new tenant pages screens.
- Deploy to staging and confirm it works there.

## Reporting

Same as before:

```bash
/home/bernardjr/.claude/skills/orchestrating-herdr-agents/scripts/herdr-say.sh \
  --state done --task CMS-UI --why "<one concrete sentence>"
```

Stop and report with `--state blocked` rather than guessing if the production image will not
build, if a product constraint blocks something here, or if the test suite fails for a reason
you did not introduce.

Do not open a pull request.
