from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from oauth2_provider.models import Application


class Command(BaseCommand):
    help = "Create or update the static confidential OAuth client used by Claude."

    @transaction.atomic
    def handle(self, *args, **options):
        client_id = settings.CLAUDE_OAUTH_CLIENT_ID.strip()
        client_secret = settings.CLAUDE_OAUTH_CLIENT_SECRET.strip()
        redirect_uris = " ".join(settings.CLAUDE_OAUTH_REDIRECT_URIS.split())

        if not all((client_id, client_secret, redirect_uris)):
            raise CommandError(
                "CLAUDE_OAUTH_CLIENT_ID, CLAUDE_OAUTH_CLIENT_SECRET, and "
                "CLAUDE_OAUTH_REDIRECT_URIS are required"
            )

        application = (
            Application.objects.filter(client_id=client_id).first()
            or Application.objects.filter(name="Claude").order_by("pk").first()
            or Application()
        )
        application.name = "Claude"
        application.client_id = client_id
        application.client_secret = client_secret
        application.client_type = Application.CLIENT_CONFIDENTIAL
        application.authorization_grant_type = Application.GRANT_AUTHORIZATION_CODE
        application.redirect_uris = redirect_uris
        application.hash_client_secret = True
        application.full_clean()
        application.save()

        Application.objects.filter(name="Claude").exclude(pk=application.pk).delete()
        self.stdout.write(self.style.SUCCESS("Claude OAuth client registered."))
