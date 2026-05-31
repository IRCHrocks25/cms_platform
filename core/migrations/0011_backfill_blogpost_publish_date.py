"""Backfill publish_date for published posts that never got one.

A published BlogPost with publish_date=NULL is invisible to the public
queries (published_posts / is_live require publish_date IS NOT NULL for
stable ordering), so it never appears in the blog index, post pages, or
homepage strip — even though the dashboard shows it as "Published". The model
now stamps a
publish_date on save, but existing rows written before that (admin, seed
scripts, the featured-star toggle on an undated post) need repair. Use
created_at so the original chronology / ordering is preserved.
"""
from django.db import migrations
from django.db.models import F


def backfill_publish_date(apps, schema_editor):
    BlogPost = apps.get_model("core", "BlogPost")
    BlogPost.objects.filter(
        status="published", publish_date__isnull=True
    ).update(publish_date=F("created_at"))


def noop_reverse(apps, schema_editor):
    # Irreversible by design: we can't tell which rows were backfilled.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0010_tenant_blog_settings_blogpost"),
    ]

    operations = [
        migrations.RunPython(backfill_publish_date, noop_reverse),
    ]
