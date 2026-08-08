"""Fail fast, and loudly, when the interpreter isn't the one this project pins.

`requirements.txt` pins ``Django==5.1.2``. On Python 3.14 that pin still
*imports* cleanly — nothing complains at startup. It breaks much later, in
``django/template/context.py``, with ``AttributeError: 'super' object has no
attribute 'dicts'`` on every test-client request that renders a template. In
practice that reads as a hundred-odd broken tests scattered across the suite
rather than "you are on the wrong Python" — 102 errors out of 614 measured on
``main`` on 2026-08-08 — and it has already cost one debugging session chasing
working code.

So the interpreter check happens before Django is ever touched, and the message
names the actual cause. Stdlib only — this has to run even when the venv is
half-built or Django can't be imported at all.

Changing the target Python means changing it in three places together:
``.python-version``, the ``FROM python:X.Y-slim`` line in the ``Dockerfile``,
and ``REQUIRED_PYTHON`` below. ``cms_platform/tests/test_python_version.py``
fails if they disagree.
"""

import sys

# The interpreter this project targets, everywhere: .python-version, the
# Dockerfile base image (python:3.12-slim) and therefore production.
REQUIRED_PYTHON = (3, 12)

# Kept in step with requirements.txt so the message below can't go stale.
PINNED_DJANGO = "5.1.2"

# Inclusive (lowest, highest) Python minor supported by PINNED_DJANGO.
# Django 5.1 supports 3.10 through 3.13; 3.14 is where it comes apart.
DJANGO_SUPPORTED_PYTHON = ((3, 10), (3, 13))

_BANNER = "=" * 78


def _format(version_info):
    return ".".join(str(part) for part in version_info[:3])


def version_mismatch_message(version_info=None, executable=None):
    """Return the error text for a wrong interpreter, or ``None`` if it's fine.

    Only major.minor is compared — patch releases are interchangeable.
    """
    version_info = tuple(version_info or sys.version_info)
    executable = executable or sys.executable

    running = version_info[:2]
    if running == REQUIRED_PYTHON:
        return None

    required = ".".join(str(part) for part in REQUIRED_PYTHON)
    running_full = _format(version_info)
    low, high = DJANGO_SUPPORTED_PYTHON

    if low <= running <= high:
        # Django would work here; the problem is that it isn't what ships.
        cause = (
            f"Django=={PINNED_DJANGO} does run on Python {running[0]}.{running[1]}, "
            f"but this project standardizes\non {required} — that is what production "
            f"runs (Dockerfile base image\npython:{required}-slim) and what "
            f".python-version pins. A green run on any other\nminor version does not "
            f"tell you the deployed stack works."
        )
    else:
        cause = (
            f"requirements.txt pins Django=={PINNED_DJANGO}, which does not support "
            f"Python {running[0]}.{running[1]}.\n\n"
            "Nothing fails at import time, which is exactly what makes it confusing: "
            "Django\nloads, the server starts, and then every test-client request that "
            "renders a\ntemplate dies in django/template/context.py with\n\n"
            "    AttributeError: 'super' object has no attribute 'dicts'\n\n"
            "That surfaces as a hundred-odd unrelated-looking test failures rather than "
            "\"wrong\ninterpreter\" (102 of 614 when this was measured), so the guard "
            "stops you here\ninstead."
        )

    return (
        f"\n{_BANNER}\n"
        f"  WRONG PYTHON — this project targets {required}, "
        f"you are running {running_full}\n"
        f"{_BANNER}\n\n"
        f"{cause}\n\n"
        f"Fix — rebuild the virtualenv on Python {required}. Both of these read the\n"
        f"committed .python-version, so neither needs the version spelled out again:\n\n"
        f"    rm -rf .venv\n"
        f"    uv venv && source .venv/bin/activate       # with uv\n"
        f"    pyenv install -s {required} && pyenv exec python -m venv .venv"
        f"   # with pyenv\n"
        f"    pip install -r requirements.txt\n\n"
        f"Interpreter in use: {executable}\n"
        f"{_BANNER}\n"
    )


def enforce_python_version(version_info=None, stream=None):
    """Abort with :func:`version_mismatch_message` if the interpreter is wrong."""
    message = version_mismatch_message(version_info)
    if message is None:
        return None

    (stream or sys.stderr).write(message)
    raise SystemExit(1)
