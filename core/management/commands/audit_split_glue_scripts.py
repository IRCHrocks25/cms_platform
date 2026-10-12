"""List block shells whose conversion moved page scripts into a block (CMS-69).

``split_shell_and_blocks`` used to append every ``<script>`` after the last
body band into that band, even scripts that sat after the footer and the
modals. Those scripts then ran before the markup they look up existed, so
footers stayed invisible and popups never opened.

A template is reported when its shell still has markup after ``<footer>`` and
one of its own blocks carries a ``<script>``:

* **Likely affected** - a block script looks up an id that exists in the shell
  but not in the block (``getElementById('fgModal')``).
* **Needs review** - block scripts exist, but none point at shell markup.
  Usually carousel init that belongs in the block; check by hand.

Read-only. Nothing is written.

    python manage.py audit_split_glue_scripts
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup
from django.core.management.base import BaseCommand

from core.models import Template

_ID_LOOKUP_RE = re.compile(
    r"""getElementById\(\s*['"]([\w-]+)['"]"""
    r"""|querySelector(?:All)?\(\s*['"]#([\w-]+)"""
)


def _ids(soup) -> set[str]:
    return {el["id"] for el in soup.find_all(id=True)}


def _has_markup_after_footer(soup) -> bool:
    footer = soup.find("footer")
    if footer is None:
        return False
    return any(
        footer not in el.parents and el.name != "script"
        for el in footer.find_all_next(True)
    )


class Command(BaseCommand):
    help = "Report block shells whose blocks hold page scripts moved by CMS-69."

    def handle(self, *args, **options):
        likely: list[str] = []
        review: list[str] = []
        for tpl in Template.objects.order_by("pk"):
            if not tpl.is_block_shell:
                continue
            shell = BeautifulSoup(tpl.html_source or "", "lxml")
            if not _has_markup_after_footer(shell):
                continue
            shell_ids = _ids(shell)

            block_lines: list[str] = []
            hit = False
            for bt in tpl.own_block_types.order_by("key"):
                frag = BeautifulSoup(bt.html_source or "", "lxml")
                scripts = frag.find_all("script")
                if not scripts:
                    continue
                frag_ids = _ids(frag)
                targets = sorted({
                    a or b
                    for s in scripts
                    for a, b in _ID_LOOKUP_RE.findall(s.string or "")
                    if (a or b) in shell_ids and (a or b) not in frag_ids
                })
                hit = hit or bool(targets)
                line = f"    block {bt.key!r}: {len(scripts)} script(s)"
                if targets:
                    line += " targeting shell " + ", ".join(f"#{t}" for t in targets)
                block_lines.append(line)
            if not block_lines:
                continue

            sites = sorted(
                set(tpl.tenants.values_list("subdomain", flat=True))
                | set(tpl.pages.values_list("tenant__subdomain", flat=True))
            )
            has_classic = any(
                "data-region" not in (html or "")
                for html in tpl.versions.values_list("html_source", flat=True)
            )
            report = "\n".join([
                f"template pk={tpl.pk} {tpl.name!r} "
                f"sites={', '.join(sites) or '-'} "
                f"classic_version={'yes' if has_classic else 'no'}",
                *block_lines,
            ])
            (likely if hit else review).append(report)

        for title, rows in (("Likely affected", likely), ("Needs review", review)):
            self.stdout.write(self.style.MIGRATE_HEADING(f"{title}: {len(rows)} template(s)"))
            for row in rows:
                self.stdout.write(row)
