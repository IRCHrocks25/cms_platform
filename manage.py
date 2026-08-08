#!/usr/bin/env python
import os
import sys

# Before anything else, and before Django is imported: refuse to run on an
# interpreter the pinned stack doesn't target. On the wrong Python the pinned
# Django imports fine and only falls apart later, in template rendering — see
# cms_platform/python_version.py.
from cms_platform.python_version import enforce_python_version  # noqa: E402

enforce_python_version()


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cms_platform.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
