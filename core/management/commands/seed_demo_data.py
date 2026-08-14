"""Create a deterministic, removable set of staging-only demonstration data."""

from __future__ import annotations

import os
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from core.models import BlogPost, Page, Template, Tenant, TenantMembership
from core.services.accounts import generate_password


User = get_user_model()

DEMO_TEMPLATE_SLUGS = (
    "demo-seed-horizon",
    "demo-seed-atelier",
    "demo-seed-field-notes",
)
DEMO_USERNAMES = (
    "demo-seed-owner",
    "demo-seed-editor-a",
    "demo-seed-editor-b",
    "demo-seed-editor-c",
)
DEMO_SUBDOMAINS = (
    "demo-northstar-operations",
    "demo-harbor-house",
    "demo-ember-pine",
    "demo-quiet-orchard",
    "demo-meridian-clinic",
    "demo-atlas-workshop",
)


def _template_html(title: str, section_count: int) -> str:
    sections = []
    for number in range(1, section_count + 1):
        section_id = f"section-{number:02d}"
        sections.append(
            f"""
            <section id="{section_id}" data-section="{section_id}"
                     data-label="Section {number:02d}" data-group="Page sections">
              <p class="eyebrow" data-edit="{section_id}.eyebrow" data-label="Eyebrow">Chapter {number:02d}</p>
              <h2 data-edit="{section_id}.title" data-label="Heading">{title} — section {number:02d}</h2>
              <p data-edit="{section_id}.body" data-type="richtext" data-label="Body copy">
                Thoughtful placeholder copy for reviewing realistic content density and editor controls.
              </p>
            </section>
            """
        )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style data-tokens>
    :root {{ --demo-accent: #2859d9; --demo-ink: #162033; --demo-paper: #f7f8fb; }}
    body {{ margin: 0; color: var(--demo-ink); background: var(--demo-paper); font-family: system-ui, sans-serif; }}
    main {{ width: min(100% - 40px, 960px); margin: auto; }}
    section {{ padding: 64px 0; border-bottom: 1px solid #d9deea; }}
    .eyebrow {{ color: var(--demo-accent); font-weight: 700; text-transform: uppercase; }}
  </style>
</head>
<body><main>{''.join(sections)}</main></body>
</html>"""


TEMPLATE_SPECS = (
    ("demo-seed-horizon", "[DEMO] Horizon editorial", 17),
    ("demo-seed-atelier", "[DEMO] Atelier portfolio", 8),
    ("demo-seed-field-notes", "[DEMO] Field Notes journal", 6),
)

SITE_SPECS = (
    {
        "subdomain": "demo-northstar-operations",
        "name": "[DEMO] Northstar International Hospitality and Destination Management Group — Southeast Asia Operations",
        "template": "demo-seed-horizon",
        "pages": 7,
        "posts": 18,
        "published": True,
        "content": {"section-01": {"title": "Hospitality, made memorable"}},
    },
    {
        "subdomain": "demo-harbor-house",
        "name": "[DEMO] Harbor House Coastal Hotel",
        "template": "demo-seed-atelier",
        "pages": 6,
        "posts": 7,
        "published": True,
        "content": {"section-01": {"title": "A slower pace by the sea"}},
    },
    {
        "subdomain": "demo-ember-pine",
        "name": "[DEMO] Ember & Pine Outdoor Goods",
        "template": "demo-seed-field-notes",
        "pages": 4,
        "posts": 0,
        "published": False,
        "content": {"section-01": {"title": "Built for the long way home"}},
    },
    {
        "subdomain": "demo-quiet-orchard",
        "name": "[DEMO] Quiet Orchard Farm Stay",
        "template": "demo-seed-horizon",
        "pages": 0,
        "posts": 4,
        "published": True,
        "content": {"section-01": {"title": "Room to breathe"}},
    },
    {
        "subdomain": "demo-meridian-clinic",
        "name": "[DEMO] Meridian Community Clinic",
        "template": "demo-seed-atelier",
        "pages": 3,
        "posts": 5,
        "published": True,
        "content": {"section-01": {"title": "Care that meets you here"}},
    },
    {
        "subdomain": "demo-atlas-workshop",
        "name": "[DEMO] Atlas Workshop & Design Studio",
        "template": "demo-seed-field-notes",
        "pages": 5,
        "posts": 4,
        "published": False,
        # Deliberately empty: renderer fallbacks should expose template defaults.
        "content": {},
    },
)

PAGE_TITLES = (
    "About the team",
    "Services and capabilities",
    "Our approach",
    "Frequently asked questions",
    "Contact and locations",
    "Community partnerships",
    "A deliberately long page title for checking truncation in narrow editor layouts and crowded management tables",
)


class Command(BaseCommand):
    help = "Create or remove namespaced staging demo data."

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Remove only the rows in the deterministic demo manifest.",
        )

    def handle(self, *args, **options):
        if os.environ.get("ALLOW_DEMO_SEED") != "1":
            raise CommandError(
                "Demo seeding is disabled. Set ALLOW_DEMO_SEED=1 only in an isolated staging environment."
            )

        if options["clear"]:
            self._clear()
            self.stdout.write(self.style.SUCCESS("Removed the staging demo-data manifest."))
            return

        self._seed()
        self.stdout.write(
            self.style.SUCCESS(
                "Staging demo data is ready: 3 templates, 6 sites, 25 pages, and 38 blog posts."
            )
        )

    @transaction.atomic
    def _clear(self):
        Tenant.objects.filter(subdomain__in=DEMO_SUBDOMAINS).delete()
        Template.objects.filter(
            tenant__isnull=True,
            slug__in=DEMO_TEMPLATE_SLUGS,
        ).delete()
        User.objects.filter(username__in=DEMO_USERNAMES).delete()

    @transaction.atomic
    def _seed(self):
        users = {}
        for index, username in enumerate(DEMO_USERNAMES):
            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    "email": f"{username}@staging.invalid",
                    "first_name": ("Morgan", "Avery", "Kai", "Riley")[index],
                    "last_name": "Demo",
                },
            )
            if created:
                user.set_password(generate_password())
                user.save(update_fields=["password"])
            users[username] = user

        templates = {}
        for slug, name, section_count in TEMPLATE_SPECS:
            template, _ = Template.objects.update_or_create(
                tenant=None,
                slug=slug,
                defaults={
                    "name": name,
                    "description": "[DEMO SEED] Staging-only annotated layout.",
                    "html_source": _template_html(name, section_count),
                    "editing_mode": Template.EDITING_EDITABLE,
                },
            )
            templates[slug] = template

        now = timezone.now()
        owner = users["demo-seed-owner"]
        editors = [users[name] for name in DEMO_USERNAMES[1:]]
        for site_index, spec in enumerate(SITE_SPECS):
            tenant, _ = Tenant.objects.update_or_create(
                subdomain=spec["subdomain"],
                defaults={
                    "name": spec["name"],
                    "template": templates[spec["template"]],
                    "owner": owner,
                    "content": spec["content"],
                    "site_settings": {"site_description": "Staging demonstration content only."},
                    "blog_settings": {
                        "template": ("cards", "magazine", "minimal")[site_index % 3],
                        "title": "Field notes",
                    },
                    "is_published": spec["published"],
                },
            )
            TenantMembership.objects.update_or_create(
                tenant=tenant,
                user=owner,
                defaults={"role": TenantMembership.ROLE_OWNER},
            )
            for editor in editors[: 1 + (site_index % len(editors))]:
                TenantMembership.objects.update_or_create(
                    tenant=tenant,
                    user=editor,
                    defaults={"role": TenantMembership.ROLE_EDITOR},
                )

            for page_index in range(spec["pages"]):
                title = PAGE_TITLES[page_index]
                Page.objects.update_or_create(
                    tenant=tenant,
                    slug=f"demo-page-{page_index + 1:02d}",
                    defaults={
                        "template": templates[spec["template"]],
                        "title": title,
                        "content": {} if page_index % 3 == 0 else {
                            "section-01": {"title": title}
                        },
                        "is_published": page_index != spec["pages"] - 1,
                        "show_in_nav": page_index < 5,
                        "nav_order": page_index,
                    },
                )

            for post_index in range(spec["posts"]):
                title = f"{('A local guide', 'Studio notes', 'Behind the work')[post_index % 3]} — volume {post_index + 1:02d}"
                BlogPost.objects.update_or_create(
                    tenant=tenant,
                    slug=f"demo-post-{post_index + 1:02d}",
                    defaults={
                        "title": title,
                        "excerpt": "A concise demonstration excerpt with enough length to test cards and wrapping.",
                        "body": "<p>Staging-only editorial copy for layout and pagination review.</p>",
                        "author": editors[post_index % len(editors)].get_full_name(),
                        "status": BlogPost.STATUS_PUBLISHED,
                        "publish_date": now - timedelta(days=post_index),
                        "featured": post_index < 3,
                        "featured_order": post_index,
                    },
                )
