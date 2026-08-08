"""Guard the interpreter pin.

These tests cover two things:

1. the *message* the guard produces — it has to name the real problem, because
   the whole reason the guard exists is that the natural failure mode
   (a hundred-odd template tests dying in ``django/template/context.py``)
   points nowhere near the actual cause;
2. the *consistency* of the three places the target Python is written down —
   ``.python-version``, the Dockerfile base image, and ``REQUIRED_PYTHON`` —
   so they can't drift apart silently.
"""

import re
from pathlib import Path

from django.test import SimpleTestCase

from cms_platform.python_version import (
    DJANGO_SUPPORTED_PYTHON,
    PINNED_DJANGO,
    REQUIRED_PYTHON,
    enforce_python_version,
    version_mismatch_message,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


class VersionMismatchMessageTests(SimpleTestCase):
    def test_matching_interpreter_produces_no_message(self):
        self.assertIsNone(version_mismatch_message((3, 12, 11, "final", 0)))

    def test_patch_level_is_irrelevant(self):
        for patch in (0, 7, 11, 99):
            with self.subTest(patch=patch):
                self.assertIsNone(version_mismatch_message((3, 12, patch)))

    def test_django_incompatible_python_names_the_django_pin(self):
        message = version_mismatch_message((3, 14, 6))

        self.assertIn("3.14.6", message)
        self.assertIn("3.12", message)
        self.assertIn(f"Django=={PINNED_DJANGO}", message)
        self.assertIn("does not support Python 3.14", message)

    def test_django_incompatible_python_describes_the_misleading_symptom(self):
        """The point of the guard: say what the confusing failure looks like."""
        message = version_mismatch_message((3, 14, 6))

        self.assertIn("'super' object has no attribute 'dicts'", message)
        self.assertIn("django/template/context.py", message)

    def test_django_compatible_but_untargeted_python_does_not_blame_django(self):
        """Django 5.1 runs fine on 3.11 — the message must not claim otherwise."""
        message = version_mismatch_message((3, 11, 9))

        self.assertIn("3.11.9", message)
        self.assertNotIn("does not support Python 3.11", message)
        self.assertIn("python:3.12-slim", message)

    def test_message_tells_you_how_to_fix_it(self):
        message = version_mismatch_message((3, 14, 6))

        self.assertIn("requirements.txt", message)
        self.assertIn(".venv", message)

    def test_message_reports_the_interpreter_actually_in_use(self):
        message = version_mismatch_message((3, 14, 6), executable="/usr/bin/python3.14")

        self.assertIn("/usr/bin/python3.14", message)


class EnforcePythonVersionTests(SimpleTestCase):
    def test_matching_interpreter_is_a_no_op(self):
        self.assertIsNone(enforce_python_version((3, 12, 11)))

    def test_mismatch_exits_nonzero_and_writes_the_message(self):
        written = []

        class _Stream:
            def write(self, text):
                written.append(text)

        with self.assertRaises(SystemExit) as caught:
            enforce_python_version((3, 14, 6), stream=_Stream())

        self.assertEqual(caught.exception.code, 1)
        self.assertIn("'super' object has no attribute 'dicts'", "".join(written))


class PinConsistencyTests(SimpleTestCase):
    """`.python-version`, the Dockerfile and REQUIRED_PYTHON must agree."""

    def test_python_version_file_matches_required_python(self):
        pinned = (REPO_ROOT / ".python-version").read_text().strip()

        self.assertEqual(pinned, ".".join(str(part) for part in REQUIRED_PYTHON))

    def test_dockerfile_base_image_matches_required_python(self):
        dockerfile = (REPO_ROOT / "Dockerfile").read_text()
        match = re.search(r"^FROM python:(\d+)\.(\d+)", dockerfile, re.MULTILINE)

        self.assertIsNotNone(match, "Dockerfile has no `FROM python:X.Y...` line")
        self.assertEqual((int(match.group(1)), int(match.group(2))), REQUIRED_PYTHON)

    def test_pinned_django_matches_requirements(self):
        requirements = (REPO_ROOT / "requirements.txt").read_text()

        self.assertIn(f"Django=={PINNED_DJANGO}", requirements)

    def test_required_python_is_inside_the_pinned_djangos_supported_range(self):
        low, high = DJANGO_SUPPORTED_PYTHON

        self.assertLessEqual(low, REQUIRED_PYTHON)
        self.assertLessEqual(REQUIRED_PYTHON, high)


class GuardWiringTests(SimpleTestCase):
    """The guard has to run *before* Django is touched, or it's pointless."""

    def test_manage_py_enforces_before_importing_django(self):
        source = (REPO_ROOT / "manage.py").read_text()
        guard_at = source.find("enforce_python_version()")
        django_at = source.find("django")

        self.assertNotEqual(guard_at, -1, "manage.py never calls enforce_python_version()")
        self.assertNotEqual(django_at, -1)
        self.assertLess(
            guard_at,
            django_at,
            "manage.py must enforce the interpreter before it imports Django",
        )

    def test_package_init_enforces_on_import(self):
        """Covers wsgi/asgi/gunicorn, which never go through manage.py."""
        source = (REPO_ROOT / "cms_platform" / "__init__.py").read_text()

        self.assertIn("enforce_python_version()", source)
