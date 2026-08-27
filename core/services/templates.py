"""CMS-27: single write path for Template HTML, ownership, and versions.

Dashboard / MCP call these helpers. ``Template.save()`` still re-derives
schema unconditionally; these guards protect product surfaces, not the DB.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional, Union

from bs4 import BeautifulSoup
from django.db import transaction
from django.utils.text import slugify

from core.models import Page, Template, TemplateVersion, Tenant
from core.parser import build_schema

logger = logging.getLogger(__name__)


def _next_unique_slug(base: str, taken: set[str]) -> str:
    """Return ``base`` or ``base-2``, ``base-3``, … not present in ``taken``."""
    base = slugify(base)[:140] or "template"
    slug = base
    i = 2
    while slug in taken:
        suffix = f"-{i}"
        slug = base[: 140 - len(suffix)] + suffix
        i += 1
    return slug


def prepare_owned_templates_for_tenant_delete(tenant: Tenant) -> int:
    """Re-slug owned templates that would collide when SET_NULL promotes them.

    CMS-27 intentionally uses ``on_delete=SET_NULL`` so a deleted site's
    templates (and their ``TemplateVersion`` history) return to the library.
    Clones from ``clone_for()`` share the library original's slug, which is
    legal while tenant-owned but violates ``uniq_library_template_slug`` on
    promotion (CMS-29). Call this before deleting ``tenant``.

    Returns the number of templates whose slug was changed.
    """
    owned = list(Template.objects.filter(tenant_id=tenant.pk).only("pk", "slug"))
    if not owned:
        return 0

    # Slugs already claimed by the library, plus every owned slug we will keep
    # or assign; siblings promote together and must not collide with each other.
    taken: set[str] = set(
        Template.objects.filter(tenant__isnull=True).values_list("slug", flat=True)
    )
    changed = 0
    for tmpl in owned:
        if tmpl.slug not in taken:
            taken.add(tmpl.slug)
            continue
        new_slug = _next_unique_slug(tmpl.slug, taken)
        Template.objects.filter(pk=tmpl.pk).update(slug=new_slug)
        taken.add(new_slug)
        changed += 1
        logger.info(
            "CMS-29: re-slugged template pk=%s %r → %r before promoting to library "
            "(tenant_id=%s subdomain=%s)",
            tmpl.pk,
            tmpl.slug,
            new_slug,
            tenant.pk,
            getattr(tenant, "subdomain", ""),
        )
    return changed


EditableTarget = Union[Tenant, Page]


class CrossTenantTemplateError(Exception):
    """Raised when assigning a template owned by a different tenant."""


class ConcurrentWriteError(Exception):
    """The template changed since the caller's expected etag.

    Raised from inside the write transaction, while the template row is locked,
    so the check cannot be overtaken between comparison and write.
    """


class FieldLossError(Exception):
    """Published site(s) would lose fields; caller must confirm.

    Carries ``drifted_fields`` / ``drift_affected`` too when the same candidate
    also trips the drift guard, so one round-trip can show the operator both
    problems. Reporting them one at a time deadlocked the form: each response
    forgot the confirmation the previous one had collected.
    """

    def __init__(
        self,
        lost_fields: set[str],
        affected: list[dict[str, Any]],
        *,
        drifted_fields: Optional[set[str]] = None,
        drift_affected: Optional[list[dict[str, Any]]] = None,
    ):
        self.lost_fields = set(lost_fields)
        self.affected = affected
        self.drifted_fields = set(drifted_fields or ())
        self.drift_affected = drift_affected or []
        fields = ", ".join(sorted(self.lost_fields))
        sites = ", ".join(
            a.get("label", "?") for a in affected
        ) or "(none)"
        super().__init__(
            f"Saving would drop fields [{fields}] used by published site(s): "
            f"{sites}. Retry with allow_field_loss=True to proceed; content "
            f"is preserved either way."
        )


class FieldDriftError(Exception):
    """An existing field id now owns a different element; caller must confirm.

    Mirrors ``FieldLossError``: carries the loss findings too when both guards
    fire on the same candidate.
    """

    def __init__(
        self,
        drifted_fields: set[str],
        affected: list[dict[str, Any]],
        *,
        lost_fields: Optional[set[str]] = None,
        loss_affected: Optional[list[dict[str, Any]]] = None,
    ):
        self.drifted_fields = set(drifted_fields)
        self.affected = affected
        self.drift_affected = affected
        self.lost_fields = set(lost_fields or ())
        self.loss_affected = loss_affected or []
        fields = ", ".join(sorted(self.drifted_fields))
        sites = ", ".join(a.get("label", "?") for a in affected) or "(none)"
        super().__init__(
            f"Saving would move fields [{fields}] onto different elements, and "
            f"published site(s) {sites} have edited copy stored against them. "
            f"The edits would land in the wrong place. Retry with "
            f"allow_field_drift=True to proceed."
        )


@dataclass
class SaveTemplateResult:
    template: Template
    version: TemplateVersion
    lost_fields: set[str] = field(default_factory=set)
    affected: list[dict[str, Any]] = field(default_factory=list)
    drifted_fields: set[str] = field(default_factory=set)
    drift_affected: list[dict[str, Any]] = field(default_factory=list)
    #: True when the submitted HTML already matched what is stored AND archived,
    #: so no TemplateVersion was appended. Callers must not report an update.
    unchanged: bool = False


def _dotted_field_ids(schema: Optional[dict]) -> set[str]:
    ids: set[str] = set()
    for section in (schema or {}).get("sections") or []:
        for f in section.get("fields") or []:
            fid = f.get("id")
            if fid:
                ids.add(fid)
    return ids


def ignored_submitted_field_markers(
    html_source: str,
    schema: Optional[dict] = None,
) -> list[str]:
    """Return submitted marker occurrences absent from the derived schema.

    Occurrence counts matter. A set comparison would miss the case where one
    valid marker and one ignored orphan share the same dotted identifier.
    """
    soup = BeautifulSoup(html_source or "", "lxml")
    submitted = [
        (element.get("data-edit") or "").strip() or "(empty data-edit)"
        for element in soup.find_all(attrs={"data-edit": True})
    ]
    derived = Counter()
    for section in (schema or build_schema(html_source)).get("sections") or []:
        for field_entry in section.get("fields") or []:
            field_id = (field_entry.get("id") or "").strip()
            if field_id:
                derived[field_id] += 1

    ignored: list[str] = []
    for marker in submitted:
        if derived[marker] > 0:
            derived[marker] -= 1
        else:
            ignored.append(marker)
    return ignored


def _content_value(content: dict, dotted: str) -> Any:
    if not dotted or not isinstance(content, dict):
        return None
    parts = dotted.split(".", 1)
    if len(parts) != 2:
        return content.get(dotted)
    section, key = parts
    nested = content.get(section)
    if isinstance(nested, dict):
        return nested.get(key)
    return None


def _value_nonempty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return True


def _target_tenant(target: EditableTarget) -> Tenant:
    if isinstance(target, Tenant):
        return target
    return target.tenant


def _sites_using_template(template: Template) -> Iterable[tuple[str, Any, dict, bool]]:
    """Yield (label, row, content, is_published) for tenants/pages on template."""
    for tenant in Tenant.objects.filter(template_id=template.pk):
        yield (
            f"site:{tenant.subdomain}",
            tenant,
            tenant.content or {},
            bool(tenant.is_published),
        )
    for page in Page.objects.filter(template_id=template.pk).select_related("tenant"):
        yield (
            f"page:{page.tenant.subdomain}/{page.slug}",
            page,
            page.content or {},
            bool(page.is_published),
        )


def _section_defaults(schema: Optional[dict]) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for section_id, fields in ((schema or {}).get("defaults") or {}).items():
        if isinstance(fields, dict):
            out[section_id] = fields
    return out


#: Ids the annotator generates positionally (``p_1``, ``li_3``, ``h3_2``). They
#: name a slot, not a thing, so inserting a block above one silently hands it a
#: different element. Semantic ids the operator wrote do not move that way.
_GENERATED_ID_RE = re.compile(r"^[a-z][a-z0-9]*_\d+$")

#: How many independent "this id now holds what that id used to hold" matches
#: it takes before a renumbering is more likely than a coincidence. One is not
#: enough: rewording a paragraph into a neighbour's old words is a normal edit,
#: and treating it as drift blocked legitimate saves.
_SHIFT_CORROBORATION = 2


def _generated_ids(section_defaults: dict[str, str]) -> set[str]:
    return {key for key in section_defaults if _GENERATED_ID_RE.match(key)}


def _drifted_ids(old_schema: Optional[dict], new_schema: Optional[dict]) -> set[str]:
    """Generated field ids that may now own a different element.

    Two signals, because neither alone is sound:

    * **The section gained generated ids.** A block was inserted and every
      ``p_N`` after it shifted down. This catches ``p_1``, repointed at the
      new block, holding text that matches nothing it held before, which is why
      comparing defaults missed it.
    * **A shift is visible in the text**, corroborated at least
      ``_SHIFT_CORROBORATION`` times: several ids each now hold what a
      *different* id used to. This catches a renumbering that left the count
      alone. The threshold is what keeps an ordinary reword from tripping it.

    Positional fingerprints cannot do better than this: ``p_2`` is still the
    second paragraph after an insertion, so its position is unchanged while its
    content is not. The real cure is stable, content-derived ids from the
    annotator; until then this is a guard, not a proof.
    """
    old_defaults = _section_defaults(old_schema)
    new_defaults = _section_defaults(new_schema)
    drifted: set[str] = set()

    for section_id, old_fields in old_defaults.items():
        new_fields = new_defaults.get(section_id)
        if not new_fields:
            continue
        old_generated = _generated_ids(old_fields)
        new_generated = _generated_ids(new_fields)
        shared = old_generated & new_generated
        if not shared:
            continue

        if len(new_generated) > len(old_generated):
            drifted |= {f"{section_id}.{key}" for key in shared}
            continue

        # What each value used to be called, so we can spot it under a new id.
        owner_before: dict[str, str] = {}
        for key, value in old_fields.items():
            if isinstance(value, str) and value.strip():
                owner_before.setdefault(value.strip(), key)
        moved = set()
        for key in shared:
            new_value = new_fields[key]
            if not isinstance(new_value, str) or new_value == old_fields[key]:
                continue
            previous_owner = owner_before.get(new_value.strip())
            if previous_owner is not None and previous_owner != key:
                moved.add(key)
        if len(moved) >= _SHIFT_CORROBORATION:
            drifted |= {f"{section_id}.{key}" for key in shared}

    return drifted


def _affected_published(
    template: Template,
    fields: set[str],
    *,
    presence_only: bool = False,
) -> list[dict[str, Any]]:
    """Published sites holding content for any of ``fields``.

    ``presence_only`` counts a stored empty string as content. For field *loss*
    an empty value is nothing to lose, but for *drift* a deliberate blanking is
    a real edit; letting it through blanks whichever element the id now owns.
    """
    affected: list[dict[str, Any]] = []
    for label, _row, content, published in _sites_using_template(template):
        if not published:
            continue
        if presence_only:
            held = [
                fid
                for fid in fields
                if _content_value(content, fid) is not None
                or _has_stored_key(content, fid)
            ]
        else:
            held = [
                fid for fid in fields if _value_nonempty(_content_value(content, fid))
            ]
        if held:
            affected.append({"label": label, "fields": held})
    return affected


def _has_stored_key(content: dict, dotted: str) -> bool:
    if not isinstance(content, dict) or "." not in (dotted or ""):
        return False
    section, key = dotted.split(".", 1)
    nested = content.get(section)
    return isinstance(nested, dict) and key in nested


def _html_etag(html: str) -> str:
    return hashlib.sha256((html or "").encode("utf-8")).hexdigest()


def _sync(caller: Template, locked: Template) -> None:
    """Copy committed state back onto the caller's instance.

    Callers keep using the object they passed in (and the dashboard renders
    from it), so it must not keep showing pre-write values.
    """
    caller.html_source = locked.html_source
    caller.schema = locked.schema
    caller.updated_at = locked.updated_at


def save_template_version(
    template: Template,
    html_source: str,
    *,
    user,
    allow_field_loss: bool = False,
    allow_field_drift: bool = False,
    label: str = "",
    expect_html_etag: Optional[str] = None,
) -> SaveTemplateResult:
    """Write HTML, append a TemplateVersion, optionally refuse field loss.

    Every read the write decision depends on comes from the row locked inside
    this transaction. Deriving them from the caller's instance let a writer
    that fetched A, waited while another writer committed B, then validate
    A -> candidate while actually overwriting B.
    """
    with transaction.atomic():
        locked = Template.objects.select_for_update().get(pk=template.pk)

        if expect_html_etag is not None and _html_etag(locked.html_source) != expect_html_etag:
            raise ConcurrentWriteError(
                "Conflict (409): template has changed since if_match. "
                "Re-read and retry with the current etag."
            )

        old_fields = _dotted_field_ids(locked.schema)
        new_schema = build_schema(html_source)
        lost = old_fields - _dotted_field_ids(new_schema)

        # Both guards are evaluated before either is raised, and each error
        # carries the other's findings, so one refusal shows the operator every
        # confirmation the save needs. Raising the first one alone made the
        # form alternate between two warnings forever.
        affected = _affected_published(locked, lost) if lost else []

        # Ids that survive but change owner. The loss guard is blind to these:
        # nothing was removed, and they are what displaced a whole site's copy
        # once already. Only worth refusing where a published site actually
        # stores edits against a drifted id; unedited copy follows the template
        # now (see renderer.strip_defaults).
        drifted = _drifted_ids(locked.schema, new_schema)
        drift_affected = (
            _affected_published(locked, drifted, presence_only=True) if drifted else []
        )

        loss_unconfirmed = bool(affected) and not allow_field_loss
        drift_unconfirmed = bool(drift_affected) and not allow_field_drift
        if loss_unconfirmed:
            # Raised inside atomic(): propagation rolls the block back, and
            # nothing has been written at this point either way.
            raise FieldLossError(
                lost,
                affected,
                drifted_fields=drifted if drift_unconfirmed else set(),
                drift_affected=drift_affected if drift_unconfirmed else [],
            )
        if drift_unconfirmed:
            raise FieldDriftError(drifted, drift_affected)

        latest = locked.versions.order_by("-number").first()
        # Only a no-op when the latest version genuinely archives the bytes that
        # are live. A direct Template.save() can move html_source without
        # cutting a version, and that drift deserves a repair version.
        if (
            latest is not None
            and latest.html_source == locked.html_source == html_source
        ):
            # No new version, but the stored schema can still be stale. Parser
            # fixes change what the same HTML derives to, and rendering merges
            # against the *stored* schema. Re-derive before returning, or an
            # operator re-pasting identical HTML gets "unchanged" and no repair.
            if locked.schema != new_schema:
                locked.schema = new_schema
                locked.save(update_fields=["schema", "updated_at"])
            _sync(template, locked)
            return SaveTemplateResult(
                template=template,
                version=latest,
                lost_fields=set(),
                affected=[],
                unchanged=True,
            )

        locked.html_source = html_source
        locked.save()  # re-derives schema
        version = TemplateVersion.objects.create(
            template=locked,
            number=(latest.number if latest is not None else 0) + 1,
            html_source=locked.html_source,
            schema=locked.schema or {},
            label=label or "",
            saved_by=user if getattr(user, "pk", None) else None,
        )
        _sync(template, locked)

    return SaveTemplateResult(
        template=template,
        version=version,
        lost_fields=lost,
        affected=affected,
        drifted_fields=drifted,
        drift_affected=drift_affected,
    )


def restore_template_version(
    template: Template,
    version: TemplateVersion,
    *,
    user,
    allow_field_loss: bool = False,
    label: str = "",
) -> SaveTemplateResult:
    """Forward-only restore: append a new version from archived HTML."""
    if version.template_id != template.pk:
        raise ValueError("version does not belong to template")

    fresh = build_schema(version.html_source)
    archived = version.schema or {}
    if fresh != archived:
        logger.warning(
            "core/parser.py has changed since this version was saved "
            "(template_id=%s version=%s); using freshly derived schema",
            template.pk,
            version.number,
        )

    restore_label = label or f"Restored from v{version.number}"
    return save_template_version(
        template,
        version.html_source,
        user=user,
        allow_field_loss=allow_field_loss,
        label=restore_label,
    )


def assign_template(
    target: EditableTarget,
    template: Template,
    *,
    user,
) -> Template:
    """Assign ``template`` to a Tenant or Page, cloning library rows."""
    tenant = _target_tenant(target)
    owner_id = template.tenant_id

    if owner_id is None:
        assigned = template.clone_for(tenant, user=user)
    elif owner_id == tenant.pk:
        assigned = template
    else:
        owner_name = getattr(template.tenant, "name", None) or f"tenant#{owner_id}"
        raise CrossTenantTemplateError(
            f"Template “{template.name}” is owned by {owner_name}. "
            f"Duplicate it into this site first, then assign the copy."
        )

    if isinstance(target, Tenant):
        target.template = assigned
        target.save(update_fields=["template", "updated_at"])
    else:
        target.template = assigned
        target.save(update_fields=["template", "updated_at"])

    return assigned
