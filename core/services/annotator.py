"""
AI-powered HTML annotator (annotations-only / JSON approach).

Takes raw HTML and returns the same HTML with `data-section`, `data-edit`,
`data-type`, etc. attributes injected so the template parser can build a
schema from it.

Why JSON instead of "echo the whole document":
  The old approach asked the model to return the entire HTML with attributes
  added, so the *output* scaled with page size. Big pages (100KB+) blew past
  the model's max output tokens (finish_reason=length) and came back truncated.

  This version never asks the model to reproduce the HTML. Instead:

  1. Strip <style>/<script> blocks (placeholders), as before — they don't need
     annotating and waste tokens.
  2. Tag every element with a unique `data-cms-ref="N"`.
  3. Send the marked HTML and ask the model for a COMPACT JSON object listing
     which refs are sections / fields and how to annotate them.
  4. Apply those attributes server-side by ref, drop the refs, restore blocks.

  The model's output is proportional to the number of annotations (a few KB),
  not the page size — so truncation no longer happens on large pages.
"""
from __future__ import annotations

import json
import logging
import re

from bs4 import BeautifulSoup
from django.conf import settings

from core.parser import VALID_FIELD_TYPES, build_schema


logger = logging.getLogger(__name__)


class AnnotatorError(Exception):
    """Raised when annotation fails (missing key, API error, invalid output)."""


_SYSTEM_PROMPT = """You annotate raw HTML so a locked-structure CMS can build a client editing UI from it.

Every element in the HTML has a unique `data-cms-ref="N"` attribute. You do NOT
rewrite or return the HTML. You return ONLY a compact JSON object that says which
elements (by their data-cms-ref number) are editable sections and fields.

Output JSON shape (and nothing else — no markdown, no prose):
{
  "sections": [
    {"ref": <int>, "id": "<snake_id>", "label": "<friendly name>", "icon": "<lucide hint>", "group": "Header|Home|Sections|Footer"}
  ],
  "fields": [
    {"ref": <int>, "edit": "<section_id>.<field_snake_id>", "type": "text|richtext|image|color|link", "label": "<friendly name>"}
  ]
}

Rules:
1. sections: one per meaningful top-level content block (hero, nav, about, features,
   pricing, gallery, testimonials, contact, footer, etc.). `ref` is the
   data-cms-ref of that block's WRAPPER element. `id` is unique, snake_case.
2. fields: every element a non-technical client would want to edit. `ref` is that
   element's data-cms-ref. `edit` is "<section_id>.<field>" and the section_id MUST
   be one you declared in "sections", and the field's element MUST be inside that
   section's wrapper element in the DOM.
3. Field types:
   - text     -> short single-line copy (headings, button labels, names)
   - richtext -> multi-line body copy / paragraphs
   - image    -> CONTENT photographs on <img> elements (binds to src). See rule 4.
   - link     -> <a> elements whose href is the editable thing (nav links, PDF links).
                 Use `text` for CTA button labels where the visible text is edited.
   - color    -> elements whose inline background-color/color is meaningful to edit
4. Images — be generous on CONTENT, strict on CHROME.
   INCLUDE every <img> whose role on the page is a real photograph or
   illustration the client would want to swap:
     - hero / banner photo
     - product shot, dish photo, room photo
     - team headshot, founder portrait, customer testimonial portrait
     - gallery / portfolio / case-study image
     - blog post feature image, "about us" photo
   When an <img> sits inside a <picture>, annotate the inner <img> (not the
   <source>) — the renderer keeps responsive candidates aligned to the new src.
   SKIP these (do NOT annotate):
     - brand logo in the nav bar or footer (chrome, set once, not per-client)
     - inline SVG icons, social-media icons, payment-method icons, app-store
       badges (decorative chrome)
     - bullet / checkmark / arrow icons inside lists or buttons
     - any <img> with alt="" or role="presentation" (declared decorative)
     - very small icons under ~32px implied by surrounding markup (e.g. inside
       a button next to a label) — these are UI affordances, not content
   When choosing the label, prefer a human-recognizable description derived
   from the alt text or the nearest heading ("Chef portrait", "Restaurant
   exterior", "Founder headshot") — avoid generic "Image 1", "Photo".
5. Body text — be EXHAUSTIVE. When in doubt, INCLUDE.
   Mark every visible piece of copy a non-technical client might want to edit:
     - every heading at any level (h1, h2, h3, h4, h5, h6)
     - every paragraph, even short ones (a 3-word tagline is still text)
     - every <li> whose text the client could change (FAQ items, feature
       bullets, footer link labels, nav link text)
     - <blockquote>, <figcaption>, <dt>, <dd>, <caption>, <legend>, <summary>
     - testimonial quote body AND the author name (two separate fields)
     - card title AND card description (two separate fields, not just title)
     - eyebrow / kicker text above headings
     - stat numbers ("1,000+") and their labels ("happy customers")
     - section sub-headings, section labels, badge text
     - any visible <span> / <strong> / <em> that holds standalone copy
   Pick the TIGHTEST element wrapping the editable copy. A <p> wrapping a
   sentence -> richtext on the <p>. An <h2> -> text on the <h2>.
   Don't lump multiple paragraphs into one richtext on a <div> wrapper —
   mark each <p> separately so the client edits them as distinct fields.
   "Short" or "small" or "repeated-looking" copy is NOT a reason to skip.
6. Repeating items (e.g. three feature cards): give each distinct field ids
   (feature_1_title, feature_2_title, ...). Do not collapse them.
7. Every section MUST contain at least one field, or it will be dropped.
8. Skip decorative/structural-only elements (spacers, layout wrappers whose
   own direct text is empty, icon-only SVGs). A wrapper whose CHILDREN hold
   visible text is NOT decorative — annotate the children. Brand-color CSS
   variables are handled automatically — ignore them.
9. ids/field ids: lowercase snake_case, [a-z0-9_] only."""


_EXAMPLE = (
    "=== EXAMPLE INPUT (marked HTML) ===\n"
    '<section data-cms-ref="0"><span data-cms-ref="1">Open daily</span>'
    '<h1 data-cms-ref="2">Bella\'s Bistro</h1>'
    '<p data-cms-ref="3">Fresh, seasonal Italian cooking.</p>'
    '<a data-cms-ref="4" href="/menu.pdf">Our menu</a>'
    '<img data-cms-ref="5" src="hero.jpg"></section>\n\n'
    "=== EXAMPLE OUTPUT (JSON) ===\n"
    '{"sections":[{"ref":0,"id":"hero","label":"Hero","icon":"star","group":"Home"}],'
    '"fields":['
    '{"ref":1,"edit":"hero.eyebrow","type":"text","label":"Eyebrow"},'
    '{"ref":2,"edit":"hero.title","type":"text","label":"Restaurant name"},'
    '{"ref":3,"edit":"hero.lede","type":"richtext","label":"Intro"},'
    '{"ref":4,"edit":"hero.menu_link","type":"link","label":"Menu link"},'
    '{"ref":5,"edit":"hero.image","type":"image","label":"Hero photo"}'
    "]}"
)


_STYLE_OR_SCRIPT_RE = re.compile(
    r"(<style\b[^>]*>.*?</style>|<script\b[^>]*>.*?</script>)",
    re.DOTALL | re.IGNORECASE,
)
_ROOT_TOKEN_RE = re.compile(r":root\s*\{[^}]*--[a-zA-Z0-9_-]+\s*:", re.DOTALL)
_STYLE_OPEN_RE = re.compile(r"^<style\b", re.IGNORECASE)
_INTER_TAG_WS_RE = re.compile(r">\s+<")

# Tags that hold body text the client would want to edit.
_BACKFILL_RICHTEXT_TAGS = {"p", "blockquote", "figcaption", "summary", "dd"}
_BACKFILL_TEXT_TAGS = {
    "h1", "h2", "h3", "h4", "h5", "h6",
    "li", "dt", "caption", "legend",
}
_BACKFILL_ALL_TAGS = _BACKFILL_RICHTEXT_TAGS | _BACKFILL_TEXT_TAGS

# Chrome ancestors: text inside these belongs to the link / button / form
# rules the model handles, not the body-text safety net.
_BACKFILL_SKIP_ANCESTORS = {
    "nav", "a", "button", "form", "select", "label",
    "script", "style", "noscript",
}
_SLUG_RE = re.compile(r"[^a-z0-9_]+")


def _strip_blocks(html: str) -> tuple[str, list[str]]:
    """Replace <style>/<script> blocks with `<!--__BLOCK_n__-->` markers."""
    blocks: list[str] = []

    def replace(match: re.Match[str]) -> str:
        idx = len(blocks)
        blocks.append(match.group(0))
        return f"<!--__BLOCK_{idx}__-->"

    slimmed = _STYLE_OR_SCRIPT_RE.sub(replace, html)
    return slimmed, blocks


def _restore_blocks(annotated: str, blocks: list[str]) -> str:
    """Re-insert the stripped blocks. Add `data-tokens` to any <style> block
    that defines `:root { --var: ... }` so the parser exposes brand colors."""
    result = annotated
    for idx, block in enumerate(blocks):
        marker = f"<!--__BLOCK_{idx}__-->"
        restored = block
        if (
            _STYLE_OPEN_RE.match(block)
            and _ROOT_TOKEN_RE.search(block)
            and "data-tokens" not in block[:300].lower()
        ):
            restored = _STYLE_OPEN_RE.sub("<style data-tokens", block, count=1)
        if marker in result:
            result = result.replace(marker, restored, 1)
        else:
            logger.warning("Annotator: marker %s missing; appending block at end.", marker)
            result = result + "\n" + restored
    return result


def _slug(value) -> str:
    if not value:
        return ""
    s = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    return _SLUG_RE.sub("", s).strip("_")


def _clean_edit(edit) -> str | None:
    if not edit or "." not in str(edit):
        return None
    section_part, field_part = str(edit).split(".", 1)
    section_id = _slug(section_part)
    field_id = _slug(field_part.replace(".", "_"))
    if not section_id or not field_id:
        return None
    return f"{section_id}.{field_id}"


def _apply_annotations(ref_map: dict, data: dict) -> int:
    """Apply the model's JSON annotations onto the marked soup. Returns the
    number of fields applied."""
    for sec in data.get("sections", []) or []:
        tag = ref_map.get(str(sec.get("ref")))
        if tag is None:
            continue
        section_id = _slug(sec.get("id"))
        if not section_id:
            continue
        tag["data-section"] = section_id
        if sec.get("label"):
            tag["data-label"] = str(sec["label"])[:120]
        if sec.get("icon"):
            tag["data-icon"] = _slug(sec["icon"]) or "square"
        if sec.get("group"):
            tag["data-group"] = str(sec["group"])[:40]

    applied = 0
    for field in data.get("fields", []) or []:
        tag = ref_map.get(str(field.get("ref")))
        if tag is None:
            continue
        edit = _clean_edit(field.get("edit"))
        if not edit:
            continue
        ftype = str(field.get("type", "text")).strip().lower()
        if ftype not in VALID_FIELD_TYPES:
            ftype = "text"
        tag["data-edit"] = edit
        tag["data-type"] = ftype
        if field.get("label"):
            tag["data-label"] = str(field["label"])[:120]
        applied += 1
    return applied


def _backfill_missed_text_fields(soup) -> int:
    """Walk each data-section and promote text-bearing tags the model
    skipped (the model is conservative on real pages; this is the safety
    net that keeps every paragraph / heading / list item editable).
    Returns the number of fields added so callers can log coverage."""
    added = 0
    for sec in soup.find_all(attrs={"data-section": True}):
        sec_id = (sec.get("data-section") or "").strip()
        if not sec_id or sec_id == "brand":
            continue

        # Track existing field IDs so synthetic IDs don't collide with
        # whatever the model already chose (e.g. model used "p_1", we
        # must skip to "p_2" rather than overwrite).
        existing_field_ids: set[str] = set()
        for el in sec.find_all(attrs={"data-edit": True}):
            edit = el.get("data-edit", "")
            if "." in edit:
                existing_field_ids.add(edit.split(".", 1)[1])

        tag_counters: dict[str, int] = {}

        for el in sec.find_all(_BACKFILL_ALL_TAGS):
            if el.get("data-edit"):
                continue

            # Skip if any ancestor between us and the section is "chrome".
            skip = False
            ancestor = el.parent
            while ancestor is not None and ancestor is not sec:
                if getattr(ancestor, "name", None) in _BACKFILL_SKIP_ANCESTORS:
                    skip = True
                    break
                ancestor = ancestor.parent
            if skip:
                continue

            text = el.get_text(strip=True)
            if not text:
                continue

            tag_counters[el.name] = tag_counters.get(el.name, 0) + 1
            n = tag_counters[el.name]
            field_id = f"{el.name}_{n}"
            while field_id in existing_field_ids:
                n += 1
                field_id = f"{el.name}_{n}"
            existing_field_ids.add(field_id)

            # Pick richtext when the tag contains inline children (a <span
            # class='accent'>, an <em>, an inline <a>) — text-type rendering
            # does `el.string = value` and would flatten the children on
            # render, breaking the visual design. Plain text-only tags
            # default to text so the editor shows a single-line input.
            has_child_tags = el.find(True) is not None
            if has_child_tags or el.name in _BACKFILL_RICHTEXT_TAGS:
                ftype = "richtext"
            else:
                ftype = "text"
            el["data-edit"] = f"{sec_id}.{field_id}"
            el["data-type"] = ftype
            label = text[:40].strip()
            if len(text) > 40:
                label += "…"
            el["data-label"] = label
            added += 1
    return added


def annotate_html(raw_html: str) -> str:
    """Send raw HTML through OpenAI and return annotated HTML.

    Raises AnnotatorError on missing key, API failure, truncated/invalid JSON,
    or output the parser can't extract any sections from.
    """
    if not raw_html or not raw_html.strip():
        raise AnnotatorError("Empty HTML — nothing to annotate.")

    api_key = settings.OPENAI_API_KEY
    if not api_key:
        raise AnnotatorError(
            "OPENAI_API_KEY is not set. Add it to .env and restart the server."
        )

    try:
        from openai import OpenAI
    except ImportError as exc:
        raise AnnotatorError("openai package is not installed. Run: pip install openai") from exc

    # 1) Strip styles/scripts so they don't consume tokens.
    slimmed_input, blocks = _strip_blocks(raw_html)

    # 2) Tag every element with a unique data-cms-ref so the model can point at
    #    elements without reproducing the HTML.
    soup = BeautifulSoup(slimmed_input, "html.parser")
    ref_map: dict[str, object] = {}
    for idx, tag in enumerate(soup.find_all(True)):
        ref = str(idx)
        tag["data-cms-ref"] = ref
        ref_map[ref] = tag
    marked_html = str(soup)

    # Collapse inter-tag whitespace ONLY for the model's view of the HTML.
    # Indented templates produce runs of newlines/spaces between elements
    # that dilute the model's signal on which refs hold real content. The
    # original soup is untouched, so the saved/output HTML still has the
    # original whitespace. `>\s+<` matches only between tags — text content
    # (including text inside <pre>) is not affected.
    marked_for_model = _INTER_TAG_WS_RE.sub("><", marked_html)

    logger.info(
        "Annotator: %d block(s) stripped; %d elements marked; input %d -> %d chars "
        "(model sees %d chars).",
        len(blocks), len(ref_map), len(raw_html), len(marked_html), len(marked_for_model),
    )

    client = OpenAI(api_key=api_key, timeout=getattr(settings, "OPENAI_TIMEOUT", 120))
    user_message = (
        f"{_EXAMPLE}\n\n"
        "=== HTML TO ANNOTATE (marked) ===\n"
        f"{marked_for_model}\n\n"
        "Return ONLY the JSON object for the HTML above."
    )

    try:
        completion = client.chat.completions.create(
            model=settings.OPENAI_ANNOTATE_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            temperature=0.1,
            max_tokens=16384,
            response_format={"type": "json_object"},
        )
    except Exception as exc:
        logger.exception("OpenAI request failed during annotation")
        raise AnnotatorError(f"OpenAI request failed: {exc}") from exc

    choice = completion.choices[0]
    finish_reason = getattr(choice, "finish_reason", "unknown")
    content = (choice.message.content or "").strip()
    logger.info(
        "Annotator: model returned %d chars (finish_reason=%s).",
        len(content), finish_reason,
    )

    if finish_reason == "length":
        raise AnnotatorError(
            "The AI response was cut off — this page has an unusually large number "
            "of editable elements. Try annotating a smaller section, or split the "
            f"page. (model={settings.OPENAI_ANNOTATE_MODEL})"
        )
    if not content:
        raise AnnotatorError("OpenAI returned an empty response.")

    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        snippet = content[:300].replace("\n", " ")
        raise AnnotatorError(
            f"AI returned invalid JSON ({exc}). Starts with: {snippet!r}"
        ) from exc

    applied = _apply_annotations(ref_map, data)

    # Remove the helper refs before producing final HTML.
    for tag in soup.find_all(attrs={"data-cms-ref": True}):
        del tag["data-cms-ref"]

    # Deterministic safety net: catch text-bearing tags inside any section
    # the model skipped. The prompt alone is not enough — the LLM is
    # conservative on real pages, especially for short paragraphs, the 2nd
    # item in a repeating card group, or text nested deep in wrappers.
    backfilled = _backfill_missed_text_fields(soup)

    annotated = _restore_blocks(str(soup), blocks)

    schema = build_schema(annotated)
    sections = [s for s in schema.get("sections", []) if s.get("id") != "brand"]
    if not sections:
        logger.warning(
            "Annotator: no sections detected. Model JSON had %d sections / %d fields, "
            "%d applied.",
            len((data.get("sections") or [])), len((data.get("fields") or [])), applied,
        )
        raise AnnotatorError(
            "AI produced no editable sections "
            f"(model={settings.OPENAI_ANNOTATE_MODEL}, finish_reason={finish_reason}, "
            f"sections={len(data.get('sections') or [])}, fields_applied={applied}). "
            "The model may have referenced elements that don't form valid sections — "
            "check the server log."
        )

    logger.info(
        "Annotator: produced %d section(s) from %d model field(s) + %d backfilled.",
        len(sections), applied, backfilled,
    )
    return annotated


def _strip_code_fences(text: str) -> str:
    """If the model wrapped output in ```...```, peel it off (defensive)."""
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    return stripped
