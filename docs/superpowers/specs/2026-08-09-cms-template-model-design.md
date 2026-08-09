# CMS-27 — Template model: optional annotation, versions, and tenant ownership

**Ticket:** CMS-27 "Template model: optional annotation, versions, and tenant ownership"
**Status:** Spec. No implementation code exists yet. Implementation is a separate dispatch.
**Session:** Interactive spec session with the owner (Bernard), 2026-08-09.
**Scope rule:** three changes, one model change. They are specced together because each one is
unsafe without the other two — versions without ownership means rolling back someone else's
template; ownership without versions means a revamp is still irreversible; and "editable" only
becomes a promise you can keep once a template has an owner who was promised it.

---

## 1. What this spec delivers

Four additions to `core/models.py::Template` plus one new model, one new service module, and a
backfill migration:

| Change | Shape |
|---|---|
| Ownership | `Template.tenant` nullable FK. `NULL` = agency library. |
| Copies | `Template.cloned_from` self-FK + `clone_for()`; clone-on-assign from the library. |
| Editable as intent | `Template.editing_mode` (`raw` \| `editable`) alongside a derived `has_editable_schema`. |
| History | New `TemplateVersion` model, self-contained `(html_source, schema)` pair per version. |
| Guards | `core/services/templates.py` — the single write path, holding the field-loss guard and the assignment guard. |

It does **not** deliver UI for browsing template versions, library curation, or any change to
`core/parser.py` / `core/renderer.py`. Those are listed in §10.

---

## 2. Production facts this is designed against

Measured 2026-08-09 against live data, not assumed.

- **96 templates, 33 tenants.**
- **68 templates have zero editable sections** (`build_schema()` returns `{"sections": [],
  "defaults": {}}`). `pk=10 'Stephanie Yee Website'` — 114 KB, un-annotated — is a live client
  site. Un-annotated templates are a supported product state, not a defect.
- **`pk=4 'AI-Consultant'` is referenced by two tenants** — the only genuinely shared row.
- **~11 templates are genuinely orphaned, not 64.**

### Correction to the ticket's orphan count

CMS-27 states "64 templates are orphaned (no tenant uses them) — mostly per-page artifacts like
'Susan Rabby — Privacy Policy', 'Stephanie Yee Website — Open Jobs'." That count queried
`Tenant.template` only and missed `Page.template` (`core/models.py:195`). Verified via the live
Katek Sites MCP during this session:

| Site | Non-home pages |
|---|---|
| capitalama-clone | 20 |
| luminivwellness | 14 |
| luminivhealth | 11 |
| stephanie-yee-website | 3 |
| susan-rabbyv1 | 2 |
| dalto-ai-advisor, ramesh-gogineni, shilpa | 1 each |
| remaining 25 sites | 0 |
| **Total** | **53** |

`susan-rabbyv1/privacy-policy` and `stephanie-yee-website/open-jobs` are **live published
pages**, not dead artifacts. 32 distinct tenant-home templates + 53 page templates = 85 of 96 in
use, each with an unambiguous owner. Only ~11 have no signal.

This matters because it turns backfill from a judgement call into a mechanical migration.

---

## 3. Owner decisions

Every fork below was resolved by Bernard in session on 2026-08-09.

| # | Fork | Decision |
|---|---|---|
| 1 | Ownership shape and backfill | **Nullable FK; `NULL` = agency library.** A tenant may reference only a template it owns or a library one. Assigning a library template clones it. Cross-tenant assignment is refused outright. |
| 2a | Version granularity | **Each version stores its own `(html_source, schema)` pair, self-contained.** No delta or previous-version-dependent storage now — "later on we can implement efficient parsing by knowing the previous, but not necessarily immediately." |
| 2b | Restore semantics | **Re-derive on restore; the stored schema is the historical record.** `Template.schema == build_schema(html_source)` stays true at all times. |
| 2c | Content whose fields disappear | **Warn, preserve, and require confirmation when a published site is affected.** Content is never deleted. |
| 3 | Is "editable" a flag or derived? | **Both, with distinct meanings.** `editing_mode` is the operator's promise; `has_editable_schema` is reality. The client editor unlocks only when both hold. |
| 4 | What "upload a copy as their own" means | **Both entry points, one code path.** Automatic clone on library assign, plus an explicit staff "Duplicate into site X" action, both via `Template.clone_for()`. |
| 5 | `create_client_account`'s `template_id` | **Library templates only, cloned into the new tenant.** Client-owned ids are rejected. |

---

## 4. Model changes — `core/models.py::Template`

### 4.1 New fields

```python
tenant = models.ForeignKey(
    "core.Tenant",
    null=True, blank=True,
    on_delete=models.SET_NULL,
    related_name="templates",
    help_text="Owning tenant. NULL means the agency library.",
)
cloned_from = models.ForeignKey(
    "self",
    null=True, blank=True,
    on_delete=models.SET_NULL,
    related_name="clones",
)

EDITING_RAW = "raw"
EDITING_EDITABLE = "editable"
EDITING_MODE_CHOICES = [
    (EDITING_RAW, "Raw — not client-editable"),
    (EDITING_EDITABLE, "Editable — released to the client"),
]
editing_mode = models.CharField(
    max_length=16, choices=EDITING_MODE_CHOICES, default=EDITING_RAW
)
```

**Why `SET_NULL` on `tenant`.** `Tenant.template` and `Page.template` are both `PROTECT`
(`core/models.py:79`, `core/models.py:195`). `CASCADE` on the new FK would make deleting a tenant
try to delete templates that the same deletion is still protecting, which is a subtle collector
interaction not worth relying on. `SET_NULL` returns a deleted client's templates to the library:
no data loss, no `ProtectedError`. The cost is that a departed client's design sits in the library
until someone purges it — see the review-queue ticket in §10.

### 4.2 Slug uniqueness becomes per-owner

Drop `unique=True` from `slug`. Add:

```python
class Meta:
    ordering = ["-updated_at"]
    constraints = [
        models.UniqueConstraint(
            fields=["tenant", "slug"],
            condition=models.Q(tenant__isnull=False),
            name="uniq_template_slug_per_tenant",
        ),
        models.UniqueConstraint(
            fields=["slug"],
            condition=models.Q(tenant__isnull=True),
            name="uniq_library_template_slug",
        ),
    ]
```

Two `UniqueConstraint`s rather than one `unique_together`, because SQL treats NULLs as distinct —
a single `(tenant, slug)` constraint would let the library hold unlimited duplicate slugs.

The auto-suffix loop in `save()` (`core/models.py:58-66`) scopes its collision check to the same
owner. Its existing comment about clients colliding on "Acme" stops being true for cross-client
collisions, which is the point. Existing slugs are already globally unique, so the migration
cannot violate either constraint.

### 4.3 Derived properties — no new stored state

```python
@property
def has_editable_schema(self) -> bool:
    return bool((self.schema or {}).get("sections"))

@property
def is_client_editable(self) -> bool:
    return self.editing_mode == self.EDITING_EDITABLE and self.has_editable_schema

@property
def annotation_status(self) -> str:
    """raw | annotation_pending | annotated_not_released | editable"""
```

| `editing_mode` | `has_editable_schema` | `annotation_status` | Meaning |
|---|---|---|---|
| `raw` | False | `raw` | Deliberately not editable. The 68 un-annotated templates today. |
| `editable` | False | `annotation_pending` | Someone promised editing and has not annotated yet. Dashboard warns. |
| `raw` | True | `annotated_not_released` | Annotated, not handed over. The state during "annotate the final version". |
| `editable` | True | `editable` | Client edits via schema/JSON/colour. |

**Enforcement surface.** `is_client_editable == False` means the client editor renders read-only
("this site isn't set up for editing yet — contact your agency") and MCP `patch_content` refuses
for a tenant-scoped token. Staff and superuser paths are unaffected — `Tenant.user_can_edit`
(`core/models.py:113`) and `core/permissions.py` are not touched.

**Default is `raw`.** Locked-by-default matches the product promise. The dashboard template form
pre-selects `editable` when the pasted HTML parses with sections, so the common case is still one
click, but the model never assumes a hand-over that nobody made.

---

## 5. `TemplateVersion` — new model

```python
class TemplateVersion(models.Model):
    template = models.ForeignKey(
        Template, on_delete=models.CASCADE, related_name="versions"
    )
    number = models.PositiveIntegerField()
    html_source = models.TextField()
    schema = models.JSONField(default=dict, blank=True)
    label = models.CharField(max_length=140, blank=True, default="")
    saved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-number"]
        constraints = [
            models.UniqueConstraint(
                fields=["template", "number"], name="uniq_template_version_number"
            )
        ]
```

**Self-contained pair.** Each row holds the full HTML and the full schema as they were at save
time. No delta encoding, no dependency on the previous row. Owner's call: efficient
previous-aware storage is a later optimization, not a launch requirement.

**Keep every version.** `ContentVersion` is rolling-10 (`dashboard/views.py:2265`) because clients
autosave constantly. Template revamps are rare, agency-driven events — 96 templates accumulated
over months. Unbounded retention is correct here; a 114 KB document times a handful of revisions
is negligible. Pruning is a ticket if storage ever bites, not a launch concern.

**Versions include the current state.** `template.versions.first()` (ordering `-number`) is the
live HTML. The backfill seeds v1 for all 96 existing templates, so history is never partial.

**Restore is forward-only.** Restoring v2 appends a new version whose `html_source` equals v2's;
it never deletes rows. This mirrors the existing `ContentVersion` restore at
`dashboard/views.py:2302-2314`, which snapshots before restoring.

**Parser drift.** On restore, `build_schema()` re-runs against the archived HTML. If the result
differs from the archived schema, log a warning — "core/parser.py has changed since this version
was saved" — and use the freshly derived schema. The archived copy is evidence, never authority.

---

## 6. `core/services/templates.py` — the single write path

Both guards live in a service module, not in `Model.save()`. `Template.save()` keeps
unconditionally re-deriving schema (`core/models.py:67`) exactly as today — that invariant is not
being weakened. Putting a confirmation gate inside `save()` would make every data script and admin
action carry hidden refusal semantics.

Consequence: management commands and Django admin can still bypass the guards. That is
deliberate and documented; the guards protect the product surfaces, not the database.

### 6.1 `save_template_version(template, html_source, *, user, allow_field_loss=False, label="")`

1. Compute `old_fields` — the dotted field ids in `template.schema` before the write.
2. Compute `new_fields` from `build_schema(html_source)`.
3. `lost = old_fields - new_fields`.
4. For each lost field, find the tenants (`Tenant.content`) and pages (`Page.content`) using this
   template that hold a **non-empty** value at that field.
5. Branch:

| Condition | Behaviour |
|---|---|
| `lost` empty | Save; append version. |
| `lost` non-empty, no affected site is published | Save; append version; return the diff for display. |
| `lost` non-empty, at least one affected site is published | Refuse unless `allow_field_loss=True`. |

Dashboard renders the refusal as an interstitial listing fields and affected sites. MCP returns a
structured error carrying the same list; the caller retries with `allow_field_loss: true`. An
agent cannot silently drop a client's copy.

**Content is never deleted.** Orphaned keys stay in `Tenant.content` / `Page.content` —
`merge_with_defaults` (`core/renderer.py:896`) simply stops reading them. This is today's silent
behaviour, and it is now load-bearing rather than accidental: it is what makes a rollback restore
the client's text instead of resurrecting an empty field.

### 6.2 `assign_template(target, template, *, user)`

`target` is a `Tenant` or a `Page`.

| `template.tenant` | Result |
|---|---|
| `None` (library) | `clone_for(target_tenant)`, then assign the clone. |
| equal to the target's tenant | Assign directly. |
| any other tenant | Raise `CrossTenantTemplateError` — message names the owner and says to duplicate first. |

### 6.3 `Template.clone_for(tenant, *, user)`

Copies `name`, `description`, `html_source`, `editing_mode`. Sets `tenant`, sets `cloned_from`,
generates a slug unique within the new owner, seeds `TemplateVersion` v1 from the source's current
HTML and schema. **Does not** inherit the source's version history — a clone's history starts at
its own v1, and `cloned_from` carries the provenance.

Backs both entry points from decision 4: automatic clone-on-library-assign, and the explicit staff
"Duplicate into site X" action.

### 6.4 Call sites to reroute

| Location | Today | After |
|---|---|---|
| `dashboard/views.py:1078` | `Template.objects.get(pk=...)` then direct assign | `assign_template()` |
| `dashboard/views.py` `tenant_create` | direct assign | `assign_template()` |
| `core/services/accounts.py:41` | `Template.objects.create(**new_template)` | owner-stamped create, or `assign_template()` for an existing id |
| `dashboard/views.py:667, 985, 1662` | `Template.objects.all()` | scoped to library + the tenant in context |
| Page create | direct assign | `assign_template()` |
| Template edit POST | `template.save()` | `save_template_version()` |
| Future `push_page` (CMS-10) | n/a | both services |

---

## 7. Backfill migration

A data migration, ordered:

1. For each `Tenant`, claim `tenant.template` if unowned.
2. For each `Page`, claim `page.template` if unowned.
3. A template referenced by **exactly one distinct tenant** across `Tenant.template` ∪
   `Page.template` is owned by that tenant.
4. A template referenced by **2+ distinct tenants** stays `NULL` (library). `pk=4 'AI-Consultant'`
   is the only known case. Both sites keep rendering; the first edit or reassign forks a private
   copy through §6.
5. Unreferenced templates → `NULL` (library).
6. `editing_mode = "editable"` where `schema["sections"]` is non-empty, else `"raw"`.
7. Seed `TemplateVersion` v1 for every template from its current `html_source` + `schema`.

Step 6 makes the migration behaviourally a no-op: today's reality becomes today's declared intent,
so no live client gains or loses editing.

Predicted outcome — ~85 claimed, ~12 library. **The migration logs its actual counts rather than
asserting these**, since the estimate assumes each of the 53 pages has its own template.

---

## 8. Security: what this closes

`create_client_account` (CMS-11, `feat/cms-create-client`, `api/mcp/tools.py:152-208`) takes a
required `template_id` and resolves it with `Template.objects.filter(pk=template_id).first()` —
no ownership check, because ownership does not exist. On 2026-08-09 a sandbox tenant created on
template 14 inherited a live client's copy and page title and briefly served them at a second
public URL.

After this spec, `template_id` must reference a library template (`tenant IS NULL`). Client-owned
ids are rejected before anything is created, and the new tenant receives a clone. A superuser
token — or an AI holding one — cannot reproduce the incident, because the id it would need is
refused at the boundary rather than trusted at the callsite.

To start a client from another client's design, staff duplicate that template into the library
first. That is a deliberate, attributable action rather than an id typed into a tool call.

---

## 9. CMS-10 `push_page` — unblocked, with constraints

CMS-10 is in Backlog, blocked on exactly these questions. It is unblocked by this spec subject to
four constraints, which belong in the ticket:

1. Templates `push_page` creates are owned by the target tenant from the first call. It can never
   target a template owned by another tenant.
2. A whole-HTML re-push appends a `TemplateVersion` rather than overwriting `html_source`.
   **This is the change that makes whole-page revamps safe** — the previous blocker was that a bad
   revamp was unrecoverable.
3. A re-push that drops fields a live page is using requires `allow_field_loss: true`, per §6.1.
4. Pushed pages default to `editing_mode="raw"`. Annotating the final version and flipping to
   `editable` is the explicit hand-over step, not a side effect of the HTML happening to carry
   `data-*` attributes.

Constraint 3 interacts with CMS-7's `if_match` etag: they guard different things. `if_match`
guards against a concurrent write; `allow_field_loss` guards against a schema shrink. A push can
need both.

---

## 10. Out of scope — separate tickets

Filed rather than widening this spec:

| Item | Why separate |
|---|---|
| Template version-browsing / restore UI | Data model lands here; the UI is its own surface. Same position `ContentVersion` was in before its UI shipped. |
| Library curation — promote a tenant template to the library | Needs a copy-vs-move decision and a scrub step for client-specific copy. |
| Review queue for templates orphaned by tenant deletion | Consequence of `SET_NULL` in §4.1. |
| `TemplateVersion` retention pruning | Only if unbounded retention ever bites. |
| Delta / previous-aware version storage | Explicitly deferred by the owner in decision 2a. |

---

## 11. Testing

| Area | Test |
|---|---|
| Migration | Fixture mirroring prod shape — a template shared by two tenants, page-owned templates, orphans, un-annotated rows. Assert the shared one stays library and each single-referrer template is claimed. |
| Migration is a no-op | No template's `is_client_editable` changes across the migration. |
| Assignment | Cross-tenant assign raises `CrossTenantTemplateError`. Library assign produces a clone with `cloned_from` set and its own v1. Same-tenant assign does not clone. |
| Slug constraints | Two tenants can both hold slug "acme". Two library templates cannot. |
| Field-loss guard | Blocked without the flag when a published site is affected; allowed with it; content preserved in both outcomes; unpublished-only loss does not block. |
| Versions | Save appends; numbers are contiguous per template; restore appends rather than deletes. |
| Restore | Re-derives schema; a stubbed parser change is logged and the fresh derivation wins. |
| `is_client_editable` | All four rows of the §4.3 matrix. |
| CMS-11 | `create_client_account` rejects a client-owned `template_id` and clones a library one. |

---

## 12. Sequencing for implementation

1. Model fields + `TemplateVersion` + constraints, schema migration.
2. Backfill data migration, with its count logging.
3. `core/services/templates.py` with both guards, fully tested against the model.
4. Reroute the §6.4 call sites.
5. `editing_mode` gate on the client editor and MCP `patch_content`.
6. `create_client_account` library-only restriction (§8).

Steps 1–2 are behaviour-preserving and safe to ship alone. Step 6 is the security fix and should
not wait long behind the rest.
