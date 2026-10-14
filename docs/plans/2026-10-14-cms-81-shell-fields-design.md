# CMS-81 block-shell fields design

## Installed finding

Jill Gentry's installed template has six Scorecard popup fields and a block
region. The parser and public renderer recognize the fields, but Kate's editor
replaces all template-level Content sections with block instances when a
template has a region. The production editor returned 200 without the popup
controls. One other installed block-shell template has four similarly hidden
footer fields.

## Approved design

Keep template-level Content sections in Kate's Content tab when the page uses
blocks. List block instances first, followed by the shell sections. The shell
sections retain their existing field IDs and use the normal field controls;
they have no block instance ID, so block add/reorder controls do not act on
them. Header, footer, and brand sections keep their existing tabs. No template
markup or stored content changes are needed.

## Verification

Add a regression test with a block region, a block instance, and a standalone
popup section. The editor must render both block and popup controls, and a save
must persist popup label and URL overrides. Run focused and full CI tests, then
deploy and repeat the installed Jill owner editor GET, save/reload, and public
popup checks. Use a rolled-back transaction for temporary acceptance values so
Jill's published copy stays unchanged. Keep CMS-81 open if any live check fails.
