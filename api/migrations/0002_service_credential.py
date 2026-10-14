from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0001_mcp_audit_log"),
    ]

    operations = [
        migrations.CreateModel(
            name="ServiceCredential",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("key_id", models.UUIDField(editable=False, unique=True)),
                ("token_hash", models.CharField(max_length=64, unique=True)),
                ("scopes", models.JSONField(default=list)),
                ("expires_at", models.DateTimeField()),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("tenant", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="service_credentials", to="core.tenant")),
            ],
        ),
        migrations.AddConstraint(
            model_name="servicecredential",
            constraint=models.UniqueConstraint(
                fields=("tenant",),
                condition=models.Q(revoked_at__isnull=True),
                name="one_active_service_key_per_tenant",
            ),
        ),
    ]
