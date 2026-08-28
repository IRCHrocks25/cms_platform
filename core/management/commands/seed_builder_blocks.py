"""Seed the primitive "builder" block library (GHL-lite) and, optionally,
allowlist it on block-shell templates.

These primitives let a client compose a page from small pieces — layout rows
(1-6 columns) plus leaf elements (headline, paragraph, image, button, form) —
inside a template shell's ``data-region`` slot. They are ordinary
:class:`~core.models.BlockType` rows: annotated HTML in, schema derived on
save, so nothing here bypasses the "schema is derived, not stored" invariant.

    # create/refresh the primitives only
    python manage.py seed_builder_blocks

    # ...and attach them to one template (pk, slug, or name)
    python manage.py seed_builder_blocks --attach 2

    # ...or to every block-shell template
    python manage.py seed_builder_blocks --attach-all-shells
"""
from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from core.models import BlockType, Template


# Each entry: key -> annotated HTML fragment. A layout row carries nested
# `data-region` column slots (no editable fields); a leaf element carries
# `data-edit` fields. Inline styles keep primitives presentable even before the
# shell's own CSS is considered.
# Leaf blocks wrap their editable element in a centered, side-padded container
# so content never hugs the page edge (rows stay full-width and supply only
# vertical rhythm, so a block inside a column is not double-padded).
_INNER_OPEN = (
    '<div style="max-width:1100px;margin-left:auto;margin-right:auto;'
    'padding-left:clamp(16px,4vw,40px);padding-right:clamp(16px,4vw,40px);">'
)
_INNER_CLOSE = "</div>"


def _row(key: str, label: str, cols: int) -> str:
    slots = "".join(
        f'<div data-region="col{i}" style="min-width:0;"></div>'
        for i in range(1, cols + 1)
    )
    return (
        f'<div data-block="{key}" data-label="{label}" data-icon="columns" '
        f'data-category="Rows" '
        f'style="max-width:1180px;margin-left:auto;margin-right:auto;'
        f'display:grid;grid-template-columns:repeat({cols},minmax(0,1fr));'
        f'gap:24px;padding:24px 0;">{slots}</div>'
    )


def _section(key: str, label: str, pad_y: str = "64px") -> str:
    """A full-width band with generous vertical padding and a single centered,
    side-padded content region. Rows/blocks dropped inside are automatically
    inset from the page edge, so content never hugs the sides. This is the
    "Section" clients reach for when a plain Row feels cramped."""
    return (
        f'<section data-block="{key}" data-label="{label}" data-icon="layout" '
        f'data-category="Layout" style="padding:{pad_y} 0;">'
        f'<div data-region="content" style="max-width:1100px;margin-left:auto;'
        f'margin-right:auto;padding-left:clamp(16px,4vw,40px);'
        f'padding-right:clamp(16px,4vw,40px);"></div>'
        f'</section>'
    )


def _leaf(key: str, label: str, icon: str, category: str, inner: str,
          pad: str = "8px 0") -> str:
    return (
        f'<div data-block="{key}" data-label="{label}" data-icon="{icon}" '
        f'data-category="{category}" style="padding:{pad};">'
        f'{_INNER_OPEN}{inner}{_INNER_CLOSE}</div>'
    )


def _hug(key: str, label: str, icon: str, category: str, inner: str,
         pad: str = "8px 0") -> str:
    """Leaf that hugs its column (GHL-style buttons) instead of a 1100px wrap."""
    return (
        f'<div data-block="{key}" data-label="{label}" data-icon="{icon}" '
        f'data-category="{category}" '
        f'style="padding:{pad};display:block;width:100%;text-align:center;">'
        f'{inner}</div>'
    )


BUILDER_BLOCKS: dict[str, str] = {
    # Structural container: a padded band with a centered inner width. Drop rows
    # or blocks inside it for consistent margins (kept separate from plain Rows).
    "section": _section("section", "Section"),
    "section-tight": _section("section-tight", "Section (tight)", pad_y="32px"),
    "row-1": _row("row-1", "1 Column", 1),
    "row-2": _row("row-2", "2 Column", 2),
    "row-3": _row("row-3", "3 Column", 3),
    "row-4": _row("row-4", "4 Column", 4),
    "row-5": _row("row-5", "5 Column", 5),
    "row-6": _row("row-6", "6 Column", 6),
    "headline": _leaf(
        "headline", "Headline", "heading", "Text",
        '<h2 data-edit="headline.text" data-type="text" data-label="Heading text" '
        'style="margin:0;font-size:2rem;line-height:1.2;font-weight:700;">'
        'Your headline</h2>',
    ),
    "subheadline": _leaf(
        "subheadline", "Sub-headline", "heading", "Text",
        '<h3 data-edit="subheadline.text" data-type="text" data-label="Sub-headline text" '
        'style="margin:0;font-size:1.35rem;line-height:1.3;font-weight:600;color:#334155;">'
        'A supporting sub-headline</h3>',
        pad="6px 0",
    ),
    "paragraph": _leaf(
        "paragraph", "Paragraph", "text", "Text",
        '<div data-edit="paragraph.body" data-type="richtext" data-label="Paragraph">'
        '<p style="margin:0;line-height:1.6;">Write a paragraph of copy here. '
        'You can make text <strong>bold</strong> or <em>italic</em>.</p></div>',
        pad="6px 0",
    ),
    "list": _leaf(
        "list", "Bullet list", "list", "Text",
        '<div data-edit="list.items" data-type="richtext" data-label="List items">'
        # list-style-position:inside keeps the bullet in the text line box, so
        # markers follow the text when the block is centred/right-aligned.
        '<ul style="margin:0;padding-left:0;line-height:1.7;list-style-position:inside;">'
        '<li>First item</li><li>Second item</li><li>Third item</li></ul></div>',
        pad="6px 0",
    ),
    "richtext": _leaf(
        "richtext", "Rich text", "text", "Text",
        '<div data-edit="richtext.body" data-type="richtext" data-label="Rich text">'
        '<p style="margin:0;line-height:1.6;">Rich text supports <strong>bold</strong>, '
        '<em>italic</em>, links and lists.</p></div>',
        pad="6px 0",
    ),
    "image": _leaf(
        "image", "Image", "image", "Media",
        '<img data-edit="image.src" data-type="image" data-label="Image" '
        'src="https://placehold.co/800x450?text=Image" alt="" '
        'style="max-width:100%;height:auto;display:block;border-radius:8px;">',
    ),
    "button": _hug(
        "button", "Button", "link", "Elements",
        '<a data-edit="button.link" data-type="link" data-label="Button link" href="#" '
        'style="display:inline-flex;flex-direction:column;align-items:center;gap:2px;'
        'padding:12px 22px;background:#2563eb;color:#fff;border-radius:8px;'
        'text-decoration:none;font-weight:600;line-height:1.2;max-width:100%;">'
        '<span data-edit="button.label" data-type="text" data-label="Button text">'
        'Get Started</span>'
        '<span data-edit="button.subtext" data-type="text" data-label="Sub text" '
        'style="font-size:12px;font-weight:500;opacity:.85;"></span>'
        '</a>',
        pad="8px 0",
    ),
    "nav-link": (
        '<div data-block="nav-link" data-label="Nav link" data-icon="link" '
        'data-category="Elements" style="display:inline-block;padding:2px 6px;">'
        '<a data-edit="navlink.text" data-type="text" data-label="Link text" '
        'href="/" style="color:inherit;text-decoration:none;font-weight:600;'
        'font-size:15px;line-height:1.2;padding:4px 2px;">Link</a></div>'
    ),
    "form": _leaf(
        "form", "Form", "mail", "Form",
        '<div data-edit="form.embed" data-type="ghl-embed" data-ghl-kind="form" '
        'data-label="Lead form"></div>',
        pad="12px 0",
    ),
}


def _slots(fmt: str, n: int, start: int = 1) -> str:
    """Repeat an HTML fragment ``fmt`` (which uses ``{i}``) n times."""
    return "".join(fmt.format(i=i) for i in range(start, start + n))


# --- Media / content / embed primitives (GHL-lite expanded library) --------- #

_DIVIDER = (
    '<hr data-edit="divider.style" data-type="select" data-label="Divider style" '
    'data-apply="style:border-top" data-default="2px solid #cbd5e1" '
    'data-options="Thin=1px solid #e5e7eb;Normal=2px solid #cbd5e1;'
    'Thick=4px solid #94a3b8;Dashed=2px dashed #cbd5e1" '
    'style="border:0;border-top:2px solid #cbd5e1;margin:0;">'
)

_SPACER = (
    '<div data-edit="spacer.height" data-type="select" data-label="Height" '
    'data-apply="style:height" data-default="48px" '
    'data-options="Small=24px;Medium=48px;Large=96px;Extra large=160px" '
    'style="height:48px;"></div>'
)

_ICON = (
    '<div data-edit="icon.glyph" data-type="text" data-label="Icon (emoji / character)" '
    'style="font-size:48px;line-height:1;text-align:center;">\u2b50</div>'
)

_VIDEO = (
    '<video data-edit="video.src" data-type="video" data-label="Video" controls '
    'playsinline style="max-width:100%;height:auto;border-radius:10px;display:block;'
    'margin:auto;"><source src="" type="video/mp4"></video>'
)

_SLIDER = (
    '<div style="display:flex;gap:12px;overflow-x:auto;scroll-snap-type:x mandatory;'
    'padding-bottom:10px;">'
    + _slots(
        '<img data-edit="slider.img{i}" data-type="image" data-label="Slide {i}" '
        'src="https://placehold.co/640x400?text=Slide+{i}" alt="" '
        'style="flex:0 0 78%;scroll-snap-align:center;border-radius:10px;'
        'object-fit:cover;">', 4)
    + "</div>"
)

_GALLERY = (
    '<div data-edit="gallery.columns" data-type="select" data-label="Columns" '
    'data-apply="style:--gcols" data-default="repeat(3,minmax(0,1fr))" '
    'data-options="2 columns=repeat(2,minmax(0,1fr));3 columns=repeat(3,minmax(0,1fr));'
    '4 columns=repeat(4,minmax(0,1fr))" '
    'style="display:grid;grid-template-columns:var(--gcols,repeat(3,minmax(0,1fr)));'
    'gap:10px;">'
    + _slots(
        '<img data-edit="gallery.img{i}" data-type="image" data-label="Photo {i}" '
        'src="https://placehold.co/400x400?text={i}" alt="" '
        'style="width:100%;aspect-ratio:1/1;object-fit:cover;border-radius:8px;">', 6)
    + "</div>"
)

_LOGOS = (
    '<div style="display:flex;flex-wrap:wrap;gap:32px;align-items:center;'
    'justify-content:center;">'
    + _slots(
        '<img data-edit="logos.logo{i}" data-type="image" data-label="Logo {i}" '
        'src="https://placehold.co/160x60?text=Logo+{i}" alt="" '
        'style="height:44px;width:auto;object-fit:contain;filter:grayscale(1);'
        'opacity:.7;">', 5)
    + "</div>"
)

_FAQ = _slots(
    '<details style="border-bottom:1px solid #e5e7eb;padding:14px 0;">'
    '<summary data-edit="faq.q{i}" data-type="text" data-label="Question {i}" '
    'style="font-weight:600;cursor:pointer;">Question {i}?</summary>'
    '<div data-edit="faq.a{i}" data-type="richtext" data-label="Answer {i}" '
    'style="margin-top:10px;color:#475569;line-height:1.6;">'
    '<p style="margin:0;">Answer to question {i}.</p></div></details>', 4)

_REVIEWS = (
    '<div data-edit="reviews.columns" data-type="select" data-label="Columns" '
    'data-apply="style:--rcols" data-default="repeat(3,minmax(0,1fr))" '
    'data-options="1 column=minmax(0,1fr);2 columns=repeat(2,minmax(0,1fr));'
    '3 columns=repeat(3,minmax(0,1fr));4 columns=repeat(4,minmax(0,1fr))" '
    'style="display:grid;grid-template-columns:var(--rcols,repeat(3,minmax(0,1fr)));'
    'gap:16px;">'
    + _slots(
        '<div style="border:1px solid #e5e7eb;border-radius:12px;padding:20px;">'
        '<div data-edit="reviews.r{i}Stars" data-type="text" data-label="Review {i} stars" '
        'style="color:#f59e0b;font-size:18px;">\u2605\u2605\u2605\u2605\u2605</div>'
        '<p data-edit="reviews.r{i}Body" data-type="text" data-label="Review {i} text" '
        'style="margin:10px 0;color:#475569;line-height:1.6;">Great experience, '
        'highly recommend!</p>'
        '<div data-edit="reviews.r{i}Name" data-type="text" data-label="Review {i} name" '
        'style="font-weight:600;">Happy Client</div></div>', 3)
    + "</div>"
)

_COUNTER = (
    '<div data-edit="counter.columns" data-type="select" data-label="Columns" '
    'data-apply="style:--ccols" data-default="repeat(3,minmax(0,1fr))" '
    'data-options="2 columns=repeat(2,minmax(0,1fr));3 columns=repeat(3,minmax(0,1fr));'
    '4 columns=repeat(4,minmax(0,1fr))" '
    'style="display:grid;grid-template-columns:var(--ccols,repeat(3,minmax(0,1fr)));'
    'gap:16px;text-align:center;">'
    + _slots(
        '<div style="min-height:220px;padding:32px 16px;border-radius:12px;'
        'display:flex;flex-direction:column;justify-content:center;align-items:center;'
        'box-sizing:border-box;width:100%;background-size:cover;background-position:center;">'
        '<div data-edit="counter.n{i}Value" data-type="text" '
        'data-label="Stat {i} value" style="font-size:2.5rem;font-weight:800;'
        'color:#2563eb;">100+</div>'
        '<div data-edit="counter.n{i}Label" data-type="text" data-label="Stat {i} label" '
        'style="color:#64748b;">Metric {i}</div></div>', 3)
    + "</div>"
)

_PRICING = (
    '<div data-edit="pricing.columns" data-type="select" data-label="Columns" '
    'data-apply="style:--pcols" data-default="repeat(3,minmax(0,1fr))" '
    'data-options="2 columns=repeat(2,minmax(0,1fr));3 columns=repeat(3,minmax(0,1fr))" '
    'style="display:grid;grid-template-columns:var(--pcols,repeat(3,minmax(0,1fr)));'
    'gap:16px;">'
    + _slots(
        '<div style="border:1px solid #e5e7eb;border-radius:14px;padding:24px;'
        'text-align:center;">'
        '<div data-edit="pricing.p{i}Name" data-type="text" data-label="Plan {i} name" '
        'style="font-weight:700;font-size:1.2rem;">Plan {i}</div>'
        '<div data-edit="pricing.p{i}Price" data-type="text" data-label="Plan {i} price" '
        'style="font-size:2.2rem;font-weight:800;margin:8px 0;">$29</div>'
        '<div data-edit="pricing.p{i}Features" data-type="richtext" '
        'data-label="Plan {i} features" style="color:#475569;line-height:1.8;'
        'margin-bottom:16px;"><ul style="list-style:none;padding:0;margin:0;">'
        '<li>Feature one</li><li>Feature two</li><li>Feature three</li></ul></div>'
        '<a data-edit="pricing.p{i}Cta" data-type="link" data-label="Plan {i} button" '
        'href="#" style="display:inline-block;padding:10px 20px;background:#2563eb;'
        'color:#fff;border-radius:8px;text-decoration:none;font-weight:600;">'
        '<span data-edit="pricing.p{i}CtaLabel" data-type="text" '
        'data-label="Plan {i} button text">Choose</span></a></div>', 3)
    + "</div>"
)

_PROGRESS = (
    '<div data-edit="progress.label" data-type="text" data-label="Label" '
    'style="font-weight:600;margin-bottom:6px;">Skill</div>'
    '<div style="background:#e5e7eb;border-radius:999px;height:14px;overflow:hidden;">'
    '<div data-edit="progress.value" data-type="select" data-label="Progress" '
    'data-apply="style:width" data-default="70%" '
    'data-options="10%=10%;25%=25%;50%=50%;75%=75%;90%=90%;100%=100%" '
    'style="height:100%;width:70%;background:#2563eb;border-radius:999px;"></div></div>'
)

_FEATURE = (
    '<div data-edit="feature.side" data-type="select" data-label="Image position" '
    'data-apply="style:flex-direction" data-default="row" '
    'data-options="Image left=row;Image right=row-reverse" '
    'style="display:flex;gap:28px;align-items:center;flex-wrap:wrap;">'
    '<img data-edit="feature.image" data-type="image" data-label="Feature image" '
    'src="https://placehold.co/560x400?text=Feature" alt="" '
    'style="flex:1 1 280px;max-width:100%;border-radius:12px;object-fit:cover;">'
    '<div style="flex:1 1 280px;">'
    '<h3 data-edit="feature.title" data-type="text" data-label="Title" '
    'style="margin:0 0 10px;font-size:1.6rem;font-weight:700;">Feature title</h3>'
    '<div data-edit="feature.body" data-type="richtext" data-label="Body" '
    'style="color:#475569;line-height:1.7;margin-bottom:16px;"><p style="margin:0;">'
    'Describe the feature and why it matters to your visitor.</p></div>'
    '<a data-edit="feature.cta" data-type="link" data-label="Button" href="#" '
    'style="display:inline-block;padding:10px 20px;background:#2563eb;color:#fff;'
    'border-radius:8px;text-decoration:none;font-weight:600;">'
    '<span data-edit="feature.ctaLabel" data-type="text" data-label="Button text">'
    'Learn more</span></a></div></div>'
)

_SOCIAL_PLATFORMS = [
    ("facebook", "Facebook", "f"),
    ("instagram", "Instagram", "IG"),
    ("x", "X / Twitter", "X"),
    ("linkedin", "LinkedIn", "in"),
    ("youtube", "YouTube", "\u25b6"),
]
_SOCIAL = (
    '<div style="display:flex;gap:12px;justify-content:center;flex-wrap:wrap;">'
    + "".join(
        '<a data-edit="social.' + p + '" data-type="link" data-label="' + lbl + ' URL" '
        'href="#" style="width:42px;height:42px;border-radius:50%;background:#0f172a;'
        'color:#fff;display:inline-flex;align-items:center;justify-content:center;'
        'text-decoration:none;font-weight:700;font-size:14px;">' + g + "</a>"
        for p, lbl, g in _SOCIAL_PLATFORMS
    )
    + "</div>"
)

_MAP = (
    '<iframe data-edit="map.src" data-type="embed" data-label="Map embed URL" '
    'src="https://www.google.com/maps?q=New+York&output=embed" '
    'style="width:100%;height:360px;border:0;border-radius:12px;" loading="lazy" '
    'referrerpolicy="no-referrer-when-downgrade"></iframe>'
)

_QR = (
    '<img data-edit="qr.src" data-type="embed" data-label="QR image URL" '
    'src="https://api.qrserver.com/v1/create-qr-code/?size=220x220&data=https://example.com" '
    'alt="QR code" style="width:220px;height:220px;display:block;margin:auto;">'
)

_CODE = (
    '<div data-edit="code.html" data-type="code" data-label="HTML / embed code">'
    '<p style="margin:0;color:#94a3b8;text-align:center;">Your embedded content '
    'appears here.</p></div>'
)

# Self-contained countdown: an inline script scoped to its own parent node reads
# the (editable) target date text and updates the display every second. Scoped
# by ``document.currentScript.parentNode`` so multiple instances don't collide.
_COUNTDOWN = (
    '<div style="text-align:center;">'
    '<div data-edit="countdown.title" data-type="text" data-label="Title" '
    'style="font-size:1.1rem;color:#475569;margin-bottom:8px;">Offer ends in</div>'
    '<div class="cms-countdown" style="font-size:2.2rem;font-weight:800;">--</div>'
    '<div data-edit="countdown.target" data-type="text" '
    'data-label="Target date (YYYY-MM-DD HH:MM)" '
    'style="font-size:12px;color:#94a3b8;margin-top:6px;">2026-12-31 23:59</div>'
    "<script>(function(){var box=document.currentScript.parentNode;"
    "var out=box.querySelector('.cms-countdown');"
    "var tEl=box.querySelector('[data-edit$=\".target\"]');"
    "function tick(){var raw=((tEl&&tEl.textContent)||'').trim().replace(' ','T');"
    "var t=Date.parse(raw);if(isNaN(t)){out.textContent='--';return;}"
    "var d=Math.max(0,t-Date.now());var s=Math.floor(d/1000);"
    "var days=Math.floor(s/86400),h=Math.floor(s%86400/3600),"
    "m=Math.floor(s%3600/60),sec=s%60;"
    "out.textContent=days+'d '+h+'h '+m+'m '+sec+'s';}"
    "tick();setInterval(tick,1000);})();</script></div>"
)


BUILDER_BLOCKS.update({
    "divider": _leaf("divider", "Divider", "minus", "Elements", _DIVIDER, pad="16px 0"),
    "spacer": _leaf("spacer", "Spacer", "move-vertical", "Elements", _SPACER, pad="0"),
    "icon": _leaf("icon", "Icon", "star", "Elements", _ICON),
    "video": _leaf("video", "Video", "video", "Media", _VIDEO),
    "slider": _leaf("slider", "Image Slider", "images", "Media", _SLIDER),
    "gallery": _leaf("gallery", "Photo Gallery", "grid", "Media", _GALLERY),
    "logos": _leaf("logos", "Logo Showcase", "building", "Media", _LOGOS),
    "faq": _leaf("faq", "FAQ", "help-circle", "Elements", _FAQ),
    "reviews": _leaf("reviews", "Reviews", "star", "Social proof", _REVIEWS),
    "counter": _leaf("counter", "Number Counter", "hash", "Social proof", _COUNTER),
    "pricing": _leaf("pricing", "Pricing Table", "table", "Elements", _PRICING),
    "progress": _leaf("progress", "Progress Bar", "bar-chart", "Elements", _PROGRESS),
    "feature": _leaf("feature", "Image Feature", "layout", "Elements", _FEATURE),
    "social": _leaf("social", "Social Icons", "share-2", "Social", _SOCIAL),
    "map": _leaf("map", "Map", "map-pin", "Embed", _MAP),
    "qr": _leaf("qr", "QR Code", "qr-code", "Embed", _QR),
    "code": _leaf("code", "Code / Embed", "code", "Embed", _CODE),
    "countdown": _leaf("countdown", "Countdown", "clock", "Countdown", _COUNTDOWN,
                       pad="16px 0"),
})


def seed_block_types():
    """Create/refresh the primitive builder blocks (idempotent).

    Returns ``(block_types, created, updated)``. Shared by the management command
    and the dashboard's "Build with blocks" template-create path so both stay in
    lockstep with ``BUILDER_BLOCKS``.
    """
    block_types, created, updated = [], 0, 0
    for key, html in BUILDER_BLOCKS.items():
        bt, was_created = BlockType.objects.get_or_create(
            key=key, defaults={"html_source": html}
        )
        bt.html_source = html
        bt.is_active = True
        # label/icon/category are re-derived from the HTML in save().
        bt.save()
        block_types.append(bt)
        created += 1 if was_created else 0
        updated += 0 if was_created else 1
    return block_types, created, updated


def _resolve_template(ident: str) -> Template:
    if ident.isdigit():
        obj = Template.objects.filter(pk=int(ident)).first()
        if obj:
            return obj
    obj = (
        Template.objects.filter(slug=ident).first()
        or Template.objects.filter(name=ident).first()
    )
    if obj is None:
        raise CommandError(f"No template matches {ident!r} (pk, slug, or name).")
    return obj


class Command(BaseCommand):
    help = "Create/refresh the primitive builder blocks and optionally allowlist them."

    def add_arguments(self, parser):
        parser.add_argument(
            "--attach",
            metavar="TEMPLATE",
            help="Allowlist the primitives on this template (pk, slug, or name).",
        )
        parser.add_argument(
            "--attach-all-shells",
            action="store_true",
            help="Allowlist the primitives on every block-shell template.",
        )

    def handle(self, *args, **opts):
        from core.services.blocks import upgrade_shell_chrome_slots

        block_types, created, updated = seed_block_types()

        self.stdout.write(
            self.style.SUCCESS(
                f"Builder blocks ready: {created} created, {updated} updated "
                f"({len(block_types)} total)."
            )
        )

        upgraded = 0
        for tpl in Template.objects.all():
            if not tpl.is_block_shell:
                continue
            new_html = upgrade_shell_chrome_slots(tpl.html_source or "")
            if new_html != (tpl.html_source or ""):
                tpl.html_source = new_html
                tpl.save()
                upgraded += 1
        if upgraded:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Opened header/footer slots on {upgraded} block-shell template(s)."
                )
            )

        targets: list[Template] = []
        if opts.get("attach"):
            targets.append(_resolve_template(opts["attach"]))
        if opts.get("attach_all_shells"):
            targets += [t for t in Template.objects.all() if t.is_block_shell]

        # De-duplicate while keeping order.
        seen = set()
        for tpl in targets:
            if tpl.pk in seen:
                continue
            seen.add(tpl.pk)
            if not tpl.is_block_shell:
                self.stdout.write(
                    self.style.WARNING(
                        f"  skipped '{tpl.name}' (pk={tpl.pk}) — not a block shell "
                        "(no data-region slot)."
                    )
                )
                continue
            tpl.allowed_block_types.add(*block_types)
            self.stdout.write(
                self.style.SUCCESS(
                    f"  attached {len(block_types)} primitives to '{tpl.name}' (pk={tpl.pk})."
                )
            )
