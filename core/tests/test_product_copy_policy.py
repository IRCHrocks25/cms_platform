from pathlib import Path

from django.test import SimpleTestCase


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
EM_DASH = chr(0x2014)
SCANNED_SUFFIXES = {".html", ".py", ".txt"}

# Exact authored-source lines whose em dashes belong to data we must preserve.
# Keep this list narrow: a new exception should name one fixture or one historical
# migration payload, never an entire file or directory.
ALLOWED_LINES = {
    "core/migrations/0021_template_ownership_and_versions.py": {
        (
            "field=models.CharField(choices=[('raw', 'Raw "
            f"{EM_DASH} not client-editable'), ('editable', 'Editable "
            f"{EM_DASH} released to the client')], default='raw', max_length=16),"
        ),
    },
    "core/tests/test_ghl_oauth_locations.py": {
        f'{{"name": "no id {EM_DASH} skipped"}},',
    },
}


class ProductCopyPolicyTests(SimpleTestCase):
    def test_product_owned_source_has_no_unapproved_em_dashes(self):
        violations = []
        for source_root in ("dashboard", "core", "templates"):
            for path in sorted((REPOSITORY_ROOT / source_root).rglob("*")):
                if not path.is_file() or path.suffix not in SCANNED_SUFFIXES:
                    continue
                relative = path.relative_to(REPOSITORY_ROOT).as_posix()
                allowed = ALLOWED_LINES.get(relative, set())
                for line_number, line in enumerate(
                    path.read_text(encoding="utf-8").splitlines(),
                    start=1,
                ):
                    if EM_DASH in line and line.strip() not in allowed:
                        violations.append(f"{relative}:{line_number}: {line.strip()}")

        self.assertEqual(
            violations,
            [],
            "Unapproved em dashes found in product-owned source:\n"
            + "\n".join(violations),
        )
