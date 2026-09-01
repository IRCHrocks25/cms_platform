# CMS-55 public homepage design

## Objective

Show an anonymous visitor to the bare CMS domain a public Katek CMS marketing
homepage instead of the login page, while preserving every tenant and
authenticated-user branch in `root_redirect`.

## Approved approach

Port the pinned `katek-ai-landing-2026-09-01.html` document directly into a
standalone Django template. Preserve its section shells, inline CSS, custom
property names, responsive layout, and useful inline interactions. Replace the
agency copy with approved Katek CMS copy from `MARKETPLACE-LISTING-FILL-IN.md`.

The alternatives were to recreate the page from screenshots or extract a new
stylesheet and component system. Both introduce needless visual drift and work
against the pinned plain-HTML source, so they are rejected.

## Page structure

The page ships seven sections in the source order:

1. Hero: client-editable websites that cannot be broken, a product walkthrough
   link, a live company-site link, and client sign-in.
2. Problem: routine website changes become support tickets.
3. Approach: editable content regions inside a locked designed template.
4. Product: live preview, plain section labels, brand colours, form selection,
   device previews, publishing, version history, and multiple isolated sites.
5. Difference: clients control content while the agency keeps the layout.
6. How: upload the finished site, let Katek CMS build the editor, then hand the
   safe editor to the client.
7. FAQ: reviewer questions answered only with facts in the approved listing.

The pinned `results` section is removed completely. It contains four stock
testimonial portraits and unsupported social proof.

## Routing and safety

The only production Python change is the final no-tenant, anonymous branch of
`core.views.root_redirect`, from `redirect("login")` to rendering the new
template. The unpublished-tenant guard and all other branches remain
byte-identical.

The page links to the existing `/login/` route. Authenticated users on the bare
domain still go to `dashboard:root`. Published tenant sites and editor access to
unpublished tenant drafts still use `_render_tenant`.

## External assets and scripts

Keep KATEK-owned image, video, and logo assets from `cdn.katalyst-crm.com`.
Remove Cookiebot because this page adds no tracking or nonessential cookie.
Remove the SOP assistant because it is unrelated to the CMS product homepage.
Keep only inline scripts that operate visible page interactions.

## Accessibility and metadata

Retain semantic sections and responsive behavior, add a skip link, meaningful
image alternatives, visible keyboard focus, reduced-motion handling, a product
title, description, and social metadata. FAQ controls remain keyboard-operable.

## Test design

Add direct route coverage for all five `root_redirect` outcomes required by the
brief. The anonymous unpublished-tenant test asserts both the login redirect and
absence of a unique draft sentinel. First run the homepage test against
`origin/main` to establish red. After implementation, temporarily revert the
homepage render hunk to prove the new homepage test fails. Temporarily mutate
each unchanged branch in isolation to prove its named regression test detects a
wrong route or leaked draft, then restore the production function exactly.

