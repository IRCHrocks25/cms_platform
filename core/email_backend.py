"""
Django email backend that sends through the Resend HTTP API instead of SMTP.

Set ``EMAIL_BACKEND = "core.email_backend.ResendBackend"``. Any Django email
(``send_mail``, password-reset machinery, ``EmailMessage.send()``) is routed
through Resend using ``settings.RESEND_API_KEY``. ``from`` defaults to
``settings.DEFAULT_FROM_EMAIL`` unless the message sets its own.
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend

logger = logging.getLogger("core")


class ResendBackend(BaseEmailBackend):
    def send_messages(self, email_messages):
        if not email_messages:
            return 0

        api_key = getattr(settings, "RESEND_API_KEY", "")
        if not api_key:
            if not self.fail_silently:
                raise RuntimeError("RESEND_API_KEY is not configured.")
            logger.warning("Resend send skipped: RESEND_API_KEY missing.")
            return 0

        import resend

        resend.api_key = api_key
        sent = 0
        for message in email_messages:
            params = {
                "from": message.from_email or settings.DEFAULT_FROM_EMAIL,
                "to": list(message.to),
                "subject": message.subject,
                "text": message.body,
            }
            if message.cc:
                params["cc"] = list(message.cc)
            if message.bcc:
                params["bcc"] = list(message.bcc)
            if message.reply_to:
                params["reply_to"] = list(message.reply_to)
            # Include an HTML alternative if the message carries one.
            for content, mimetype in getattr(message, "alternatives", []) or []:
                if mimetype == "text/html":
                    params["html"] = content
                    break
            try:
                resend.Emails.send(params)
                sent += 1
            except Exception:
                logger.exception("Resend send failed for %s", params.get("to"))
                if not self.fail_silently:
                    raise
        return sent
