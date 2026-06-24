from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0016_ghl_install"),
    ]

    operations = [
        migrations.CreateModel(
            name="EmbeddableAssistant",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120)),
                ("slug", models.SlugField(blank=True, max_length=140, unique=True)),
                ("description", models.CharField(blank=True, max_length=260)),
                ("brand", models.CharField(default="Assistant", max_length=80)),
                ("brand_full", models.CharField(blank=True, max_length=140)),
                ("greeting", models.CharField(default="Hi there! How can I help you today?", max_length=240)),
                ("suggestions", models.CharField(blank=True, default="What do you offer?|How does onboarding work?|How do I contact support?", help_text="Pipe-separated quick prompts (e.g. Pricing|Book a demo|Contact support).", max_length=500)),
                ("powered_by", models.CharField(blank=True, max_length=120)),
                ("logo_url", models.URLField(blank=True, default="", max_length=600)),
                ("orb_logo_url", models.URLField(blank=True, default="", max_length=600)),
                ("launcher_label", models.CharField(default="Need help? Ask us!", max_length=120)),
                ("voice", models.CharField(default="marin", max_length=40)),
                ("extra_instructions", models.TextField(blank=True, default="")),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["-updated_at"],
            },
        ),
    ]
