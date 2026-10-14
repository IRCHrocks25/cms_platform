import uuid

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0028_customdomain_acme_health")]

    operations = [
        migrations.CreateModel(
            name="BusinessFormBinding",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("business_id", models.UUIDField()),
                ("receiver_url", models.URLField(max_length=600)),
                ("signing_secret_ciphertext", models.CharField(max_length=700)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("tenant", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="business_form_binding", to="core.tenant")),
            ],
        ),
        migrations.CreateModel(
            name="FormSubmission",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("event_id", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("business_id", models.UUIDField()),
                ("form_id", models.SlugField(max_length=80)),
                ("name", models.CharField(blank=True, max_length=200)),
                ("email", models.EmailField(blank=True, max_length=254)),
                ("phone", models.CharField(blank=True, max_length=40)),
                ("message", models.TextField(blank=True)),
                ("consent", models.BooleanField(default=False)),
                ("source", models.JSONField(default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("page", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to="core.page")),
                ("tenant", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="form_submissions", to="core.tenant")),
            ],
        ),
        migrations.CreateModel(
            name="FormDelivery",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("pending", "Pending"), ("delivered", "Delivered")], default="pending", max_length=16)),
                ("attempts", models.PositiveIntegerField(default=0)),
                ("next_attempt_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("delivered_at", models.DateTimeField(blank=True, null=True)),
                ("last_error", models.CharField(blank=True, max_length=300)),
                ("submission", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="delivery", to="core.formsubmission")),
            ],
            options={"indexes": [models.Index(fields=["status", "next_attempt_at"], name="core_form_due_idx")]},
        ),
    ]
