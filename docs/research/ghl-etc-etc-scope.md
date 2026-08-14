# Is CMS-42's "etc etc" bounded by the embed-code rule?

**Research date:** 2026-08-14. **Answer:** Yes, with one important meaning of
the rule made explicit: a share/invite/portal URL means a **styled link-out**,
not that the destination is an embeddable widget. HighLevel's current product
model puts Courses (the current form of Memberships), Communities, and a
sub-account's Affiliate Manager portal inside its hosted **Client Portal**.
HighLevel documents portal URLs, invitation links, and authenticated magic
links for those experiences; it does not document embed code for placing the
portal apps inside an external public website.

No named client-facing surface found in the current official documentation
requires a third integration primitive beyond (1) render HighLevel-provided
embed code in a slot or at site scope, or (2) render a HighLevel-provided URL
as a link that leaves the Katek site. Therefore decision 3 does not expand
CMS-42 phase 1. Portal destinations must not be promised as slot-sized widgets.

## 1. Mechanism and confidence table

This table separates what the official documentation positively verifies from
absence-of-evidence conclusions. "No evidence found" means the current
HighLevel help material describes a hosted URL/link flow and this research
found no official external-site embed instructions; it is not proof that an
undocumented iframe can never load.

| Client-facing surface | HighLevel-provided mechanism | Public-site embed? | Katek rendering | Confidence |
| --- | --- | --- | --- | --- |
| Client Portal (umbrella) | Hosted `clientclub.net` URL or sub-account custom domain; the dashboard exposes a copyable portal URL | **No evidence found** for an external-site embed code | Styled anchor/link-out | **Verified** hosted URL; embed absence is **no evidence found** |
| Memberships / Courses | Authenticated Courses app inside Client Portal; legacy Memberships migrate into Client Portal; app-specific, contact-specific SSO magic link is available | **No evidence found** | Styled anchor/link-out to the portal, invitation, or per-contact magic link | **Verified** hosted app and links; embed absence is **no evidence found** |
| Communities | Distinct Communities child app inside Client Portal; custom portal domain; group invite URL; app-specific magic link | **No evidence found** | Styled anchor/link-out to portal/group invite; private groups still require approval | **Verified** hosted app and links; embed absence is **no evidence found** |
| Affiliate Manager portal (a client's affiliates) | Distinct Affiliates child app inside Client Portal; campaign signup link, invitation, login, and app-specific magic link | **No evidence found** | Styled anchor/link-out to signup or portal | **Verified** hosted app and links; embed absence is **no evidence found** |
| Subscription, invoice, and transaction management | Authenticated Billing & Subscription area inside Client Portal | **No evidence found** | Link-out to Client Portal; not a public widget | **Verified** hosted portal feature; embed absence is **no evidence found** |
| Reviews | Generated embed code copied into an HTML/code block; explicitly supported on non-HighLevel sites | **Yes** | Embed-code slot | **Verified** |
| Quizzes | `Integrate` offers iframe embed code and a direct share link | **Yes** | Iframe slot, with link-out as fallback | **Verified** |
| Prospecting / marketing-audit widget | Generated embed code from the paid Prospecting tool | **Yes** | Embed-code slot, subject to product entitlement | **Verified** |
| Chat, including inline Live Chat and Voice AI | Generated script/embed code; Live Chat can render inline or float; Voice AI uses the same widget family | **Yes** | Inline slot only when configured inline; otherwise site-scoped script | **Verified** |
| Gift-card purchase | Dedicated checkout URL, generated embed code, QR code, or an existing checkout | **Yes**, with a share URL alternative | Embed-code slot or styled anchor/link-out | **Verified** |
| Forms, surveys, calendars, and payment links | Existing CMS-42 baseline: iframe/embed code for forms, surveys, and calendars; hosted checkout URL for payment links | **Yes**, or a share URL | Existing slot model | **Verified or qualified in the ticket and prior research** |
| Funnel order forms / full funnels, websites, and stores | Published full-page experience; no standalone order-form widget was found | **No evidence found** for a standalone order-form embed | Link to the published page | **Previously established on CMS-42**; embed absence remains an absence conclusion |

The first four rows are the named-list answer. The remaining rows are a bounded
scan of current HighLevel surfaces that official documentation itself presents
as website widgets, shareable customer experiences, or Client Portal apps. It
is not a claim to inventory every HighLevel feature.

## 2. Memberships now resolves to Courses in Client Portal

HighLevel has not removed the word **Memberships** from navigation or support
material: current instructions still say `Memberships > Courses`. The product
boundary has nevertheless changed. HighLevel's [legacy migration guide](https://help.gohighlevel.com/support/solutions/articles/155000002045)
(modified 2026-05-12) says legacy Memberships migrate to Client Portal and
describes the newer portal as the unified home for courses, communities,
affiliates, subscription management, and other client apps.

The current [Client Portal setup guide](https://help.gohighlevel.com/support/solutions/articles/155000000193-how-to-set-up-the-client-portal-)
(modified 2026-05-12) calls the portal a client-side interface on
`clientclub.net`, permits a custom domain, and tells operators to share the
portal URL. Existing membership users log in to the portal with their existing
credentials. The [Client Portal dashboard guide](https://help.gohighlevel.com/support/solutions/articles/155000001205-client-portal-dashboard)
(modified 2026-03-25) likewise exposes a copyable Client Portal URL plus
contact-level magic links and login emails.

This is a full authenticated learning experience: course library, lessons,
progress, quizzes/assignments, comments, and account state. The official docs
found here do not offer an external-site embed code for a Course or the Course
app. The supported Katek treatment is a button or link to the portal. If a
frictionless authenticated destination is required, HighLevel's
[SSO magic-link guide](https://help.gohighlevel.com/support/solutions/articles/155000001667/)
(modified 2025-07-31) documents app-specific, contact-specific URLs, but those
must be generated/distributed per contact and must not be stored as a public
page's shared slot value.

**Finding:** hosted-page/link flow, not a fifth widget kind. **Confidence:**
hosted flow **verified**; no supported public-site embed **no evidence found**.

## 3. Communities is distinct, but uses the same hosted portal boundary

Communities remains a distinct Client Portal child app, not merely another
name for Courses. The current [community member-management guide](https://help.gohighlevel.com/support/solutions/articles/155000000289-how-to-manage-members-inside-groups)
(modified 2026-03-12) places it at `Memberships > Communities > Groups` and
documents copying an Invite Link or distributing the group URL. Public groups
can be viewed and joined immediately; private-group join attempts require
approval even when the visitor arrived through an invite link.

The official [white-label Community guide](https://help.gohighlevel.com/support/solutions/articles/155000004156-how-to-create-your-whitelabel-community)
states that a Community is hosted inside Client Portal and can use a custom
domain such as `community.example.com`. The Client Portal SSO documentation
also lists Communities as its own magic-link destination.

This is affirmative evidence for hosted URLs and link sharing. This research
found no official instructions for embedding an entire Community group into an
external website. A Katek page can link to the group/portal, but it should not
render the Community as an iframe merely because a URL exists.

**Finding:** distinct product, same hosted-page/link-out handling.
**Confidence:** hosted flow **verified**; no supported public-site embed
**no evidence found**.

## 4. "Affiliate portal" has two meanings; the relevant one is link-out

HighLevel documents both its own program for agencies and Affiliate Manager,
which lets a sub-account run a program for its own affiliates. The latter is
the plausible CMS-42 client-site requirement.

The [Affiliate Manager portal guide](https://help.gohighlevel.com/support/solutions/articles/155000003650-how-to-use-the-affiliate-portal-a-comprehensive-guide-for-affiliates)
(modified 2025-04-10) calls the Affiliate Portal a dedicated platform, also
called Client Portal, where affiliates log in to see leads, customers,
commissions, payouts, and sub-affiliates. The Client Portal setup guide tells
operators to copy a campaign signup link, while the
[affiliate FAQ](https://help.gohighlevel.com/support/solutions/articles/155000003654-faqs-for-affiliates)
documents email invitations, signup, login, and the portal dashboard. Affiliates
is also a supported app-specific SSO magic-link destination.

HighLevel's own agency referral program has a separate hosted login at
`affiliate.gohighlevel.com`; that is not a client sub-account's Affiliate
Manager portal and should not be confused with it.

No external-site embed code is documented for either affiliate dashboard. A
public client site may link to a campaign signup page or portal login. An
authenticated dashboard belongs in the hosted portal, not in a public CMS slot.

**Finding:** hosted signup/login/dashboard flow. **Confidence:** hosted flow
**verified**; no supported public-site embed **no evidence found**.

## 5. Other plausible "etc etc" items

The current docs do contain several genuine website widgets beyond the items
already named on CMS-42. Each satisfies the boundary literally:

- **Reviews:** HighLevel says its generated code can be pasted into almost any
  external website and copied from the widget's Code action. See
  [Customizing Review Widgets](https://help.gohighlevel.com/support/solutions/articles/155000000997-customizing-review-widgets-in-reputation)
  (modified 2026-05-25).
- **Quizzes:** the Quiz Builder's Integrate action offers both iframe embed code
  and a direct link. See [Quiz Builder](https://help.gohighlevel.com/support/solutions/articles/155000004126-quiz-builder)
  (modified 2026-07-28).
- **Prospecting widgets:** the paid Prospecting tool generates embed code for a
  marketing-audit lead form. This is real but entitlement-dependent, and is
  more agency/SaaS-specific than a normal local-business site. See
  [Create and Customize Prospecting Widgets](https://help.gohighlevel.com/support/solutions/articles/155000002737-create-and-customize-prospecting-widgets-for-your-website-in-highlevel)
  (modified 2026-05-09).
- **Chat / Voice AI:** HighLevel provides script code for external sites. A
  newer Live Chat mode can render inline in page content; the traditional
  floating chat bubble remains site-scoped. See
  [Create an Embedded Live Chat Widget](https://help.gohighlevel.com/support/solutions/articles/155000007601-how-to-create-an-embedded-live-chat-widget)
  and [Voice AI Chat Widget](https://help.gohighlevel.com/support/solutions/articles/155000006056-voice-ai-chat-widget).
- **Gift cards:** HighLevel documents a dedicated purchase link, generated
  embed code, QR code, and reuse in existing checkouts. See
  [Sell Gift Cards](https://help.gohighlevel.com/support/solutions/articles/155000006986).

Reviews, quizzes, and inline chat demonstrate an implementation detail that
should remain explicit in future tickets: "embed code" is broader than
"iframe URL." Some official embeds are scripts. The rule is still a sufficient
product boundary, but a future renderer must parse/allowlist each supported GHL
mechanism rather than execute arbitrary pasted HTML or JavaScript. Floating
chat is still a site-scoped concern even though HighLevel supplies embed code.

The authenticated Client Portal's Billing & Subscription area is another
plausible customer-facing request. HighLevel's
[Subscription Management guide](https://help.gohighlevel.com/support/solutions/articles/155000003204)
documents subscription cancellation and invoice/transaction history inside
the portal. It therefore follows the same link-out rule as Courses,
Communities, and Affiliates; it is not a public payment widget.

## 6. Verdict for CMS-42 decision 3

**The embed-code/share-link rule is sufficient as CMS-42's product boundary.**
The named list does not reveal a separate integration model:

1. When HighLevel provides iframe/script embed code, Katek can support it as an
   explicitly allowlisted slot kind or, for global widgets, a site setting.
2. When HighLevel provides a share, invite, purchase, portal, or magic-link URL,
   Katek can present a styled anchor and let HighLevel host the destination.
3. When neither exists, the feature is outside the promise until HighLevel
   publishes a supported mechanism.

Courses/Memberships, Communities, Affiliate Manager's portal, and subscription
management are all case 2. They need **link-out handling**, but that is already
inside the stated share-link branch of the rule; they do not justify another
slot renderer or any change to CMS-42 phase 1. Funnel order forms remain the
same case: link to the hosted funnel rather than inventing an order-form widget.

Two guardrails should accompany the decision:

- A URL is not permission to iframe a page. Only advertise an embedded widget
  when HighLevel explicitly supplies embed code or documents external-site
  embedding; otherwise render the URL as a link.
- Do not place contact-specific SSO magic links in public/shared site content.
  They are an optional authenticated delivery mechanism for known contacts,
  not a generic CMS value.

**Overall confidence: verified for the documented mechanisms and current
product grouping; no evidence found for external-site embedding of Client
Portal child apps.** A live authenticated sub-account was not used, and no
attempt was made to rely on undocumented routes or browser behavior.
