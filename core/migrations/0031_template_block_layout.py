from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0030_blogpost_category")]

    operations = [
        migrations.AddField(
            model_name="template",
            name="block_layout",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
