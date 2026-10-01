from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0028_customdomain_acme_health"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="tenant",
            name="global_tenant_id",
            field=models.UUIDField(blank=True, editable=False, null=True, unique=True),
        ),
        migrations.CreateModel(
            name="VerifiedUserEmail",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("normalized_email", models.EmailField(max_length=254, unique=True)),
                ("source", models.CharField(max_length=40)),
                ("verified_at", models.DateTimeField(auto_now_add=True)),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="verified_email_proof",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="CentralIdentityLink",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("issuer", models.URLField(max_length=500)),
                ("subject", models.CharField(max_length=255)),
                ("email_at_link", models.EmailField(max_length=254)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="central_identity_links",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("issuer", "subject"),
                        name="uniq_central_identity_issuer_subject",
                    ),
                    models.UniqueConstraint(
                        fields=("issuer", "user"),
                        name="uniq_central_identity_issuer_user",
                    ),
                ],
            },
        ),
    ]
