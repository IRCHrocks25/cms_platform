"""Cache-busting for static assets.

The dev/prod static layer serves ``/static`` with a multi-hour cache and no
content hashing, so a browser keeps running an old ``editor.js`` / ``editor.css``
after a deploy until the cache naturally expires. ``static_v`` appends the
file's modification time as a ``?v=`` query string, so the URL changes whenever
the file changes and the browser is forced to refetch.
"""
import os

from django import template
from django.contrib.staticfiles import finders
from django.templatetags.static import static

register = template.Library()


@register.simple_tag
def static_v(path: str) -> str:
    url = static(path)
    abs_path = finders.find(path)
    if abs_path and os.path.exists(abs_path):
        try:
            mtime = int(os.path.getmtime(abs_path))
        except OSError:
            return url
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}v={mtime}"
    return url
