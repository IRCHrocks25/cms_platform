"""Expose Jill Gentry's three Scorecard popup choices in the client editor.

Dry run by default. Production usage after reviewing the current installed
template and tenant overrides::

    python manage.py annotate_jill_scorecard_popup
    python manage.py annotate_jill_scorecard_popup --apply
"""

from __future__ import annotations

import hashlib

from bs4 import BeautifulSoup
from django.core.management.base import BaseCommand, CommandError

from core.models import CustomDomain
from core.services.templates import save_template_version


DOMAIN = "jillgentryandco.com"
ROLES = {
    "corporate": ("Corporate Sales Director", "Corporate Sales Director quiz URL"),
    "executive": ("Executive Director", "Executive Director quiz URL"),
    "sales": ("Community Sales Director", "Community Sales Director quiz URL"),
}


def _set_marker(element, name: str, expected: str) -> bool:
    current = element.get(name)
    if current is not None:
        if current != expected:
            raise CommandError(
                f"Refusing conflicting {name}={current!r}; expected {expected!r}."
            )
        return False
    element[name] = expected
    return True


def annotate_scorecard_popup(html: str) -> str:
    """Add only editor metadata to the known Scorecard role list.

    Existing modal fields stay outside this new section, so their identifiers
    and section ownership do not change. Refuse any changed/ambiguous structure
    instead of guessing which link a client meant to edit.
    """
    soup = BeautifulSoup(html or "", "lxml")
    modals = soup.select("#scorecardModal")
    if len(modals) != 1:
        raise CommandError("Expected exactly one #scorecardModal.")
    wrappers = modals[0].select(".roles")
    if len(wrappers) != 1:
        raise CommandError("Expected exactly one .roles inside the Scorecard modal.")
    wrapper = wrappers[0]
    if wrapper.find_parent(attrs={"data-section": True}):
        raise CommandError("Scorecard roles already belong to another CMS section.")

    anchors = wrapper.select("a.role[data-quiz]")
    if len(anchors) != len(ROLES) or {a.get("data-quiz") for a in anchors} != set(ROLES):
        raise CommandError("Scorecard roles differ from the three expected quiz choices.")
    if len(wrapper.select("a.role")) != len(ROLES):
        raise CommandError("Scorecard contains an unexpected role anchor.")

    changed = False
    changed |= _set_marker(wrapper, "data-section", "scorecard_popup")
    changed |= _set_marker(wrapper, "data-label", "Scorecard popup")
    for anchor in anchors:
        role = anchor["data-quiz"]
        titles = anchor.select(".r-title")
        if len(titles) != 1 or not anchor.get("href"):
            raise CommandError(f"Scorecard {role} needs one title and a URL.")
        label, url_label = ROLES[role]
        changed |= _set_marker(anchor, "data-edit", f"scorecard_popup.{role}_url")
        changed |= _set_marker(anchor, "data-type", "link")
        changed |= _set_marker(anchor, "data-label", url_label)
        changed |= _set_marker(titles[0], "data-edit", f"scorecard_popup.{role}_label")
        changed |= _set_marker(titles[0], "data-type", "text")
        changed |= _set_marker(titles[0], "data-label", label)
    return str(soup) if changed else html


class Command(BaseCommand):
    help = "Annotate Jill Gentry's Scorecard role links (dry run unless --apply)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply", action="store_true",
            help="Save a new version of the installed Jill template.",
        )

    def handle(self, *args, **options):
        domain = (
            CustomDomain.objects.select_related("tenant__template")
            .filter(domain=DOMAIN)
            .first()
        )
        if domain is None:
            raise CommandError(f"No installed CMS domain {DOMAIN!r} was found.")
        tenant = domain.tenant
        template = tenant.template
        if template.tenant_id != tenant.pk:
            raise CommandError("Jill's homepage template must be tenant-owned.")
        old_html = template.html_source or ""
        new_html = annotate_scorecard_popup(old_html)
        if new_html == old_html:
            self.stdout.write("Scorecard popup is already annotated; no change.")
            return

        before_hash = hashlib.sha256(old_html.encode("utf-8")).hexdigest()
        if not options["apply"]:
            self.stdout.write(
                f"Would annotate three Scorecard labels and URLs in template "
                f"{template.pk} ({template.name!r}); current SHA-256 {before_hash}. "
                "Review the live template and rerun with --apply."
            )
            return

        save_template_version(
            template, new_html, user=None, preserve_rendered=True,
            label="CMS-81 Scorecard popup editable role links",
            expect_html_etag=before_hash,
        )
        self.stdout.write(self.style.SUCCESS(
            f"Annotated three Scorecard labels and URLs in template {template.pk}."
        ))
