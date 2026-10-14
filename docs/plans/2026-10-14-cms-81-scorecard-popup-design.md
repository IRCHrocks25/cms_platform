# CMS-81 Scorecard popup editing design

## Goal

Let Jill Gentry's client edit the three Scorecard role labels and destinations in the Kate editor without changing the popup's appearance or behavior.

## Evidence and constraints

The public homepage's `#scorecardModal` has three `.role` anchors. Each contains decorative SVG and other text, with the visible role name in `.r-title`. None of those anchors or titles has a CMS field marker. The modal is outside every `data-section`; its existing `auto.n9` through `auto.n12` text markers must retain their IDs and DOM owners. The parser only exposes fields inside a `data-section`.

## Options considered

1. **Focused annotation of the role list (chosen).** Add `data-section="scorecard_popup"` to `.roles`, a `link` field on each anchor, and a `text` field on each `.r-title`. Existing modal fields stay outside the new section. The renderer changes an anchor's `href` without rewriting its children, and changes title text without touching the SVG or subtitle. A guarded, dry-run-first management command applies the markers to the installed template through the versioned template service.
2. Change the generic annotator to discover links in every modal. This could help future sites but risks annotating decorative or navigation links in unrelated templates. It still needs a migration for Jill's stored template.
3. Make each whole anchor one rich-text field. Editing its label could rewrite the SVG, subtitle, and layout markup.

## Data flow and safety

The command locates exactly one Jill tenant by its installed custom domain, then requires exactly one `#scorecardModal`, one `.roles` container, and one anchor for each known `data-quiz` value. It refuses unexpected markup or existing conflicting markers and is idempotent. It first reports the changes; `--apply` writes a new template version using `save_template_version(..., preserve_rendered=True)`. That guard compares rendered defaults and all tenant overrides before and after annotation and prevents silent content changes. The command does not edit tenant content or quiz URLs.

Field IDs use `scorecard_popup.<role>_url` and `scorecard_popup.<role>_label`. Link fields use the existing safe URL policy. The new section's label is “Scorecard popup.”

## Acceptance checks

Tests cover schema fields, rendering after independent URL and label edits, safety/idempotence, and refusal when markup changes. Browser and installed-account checks exercise saving through Kate admin, reload, the public modal at desktop/mobile widths, keyboard access, and a real URL update restored after verification. Those checks remain open until the command is deployed and the installed account is available.
