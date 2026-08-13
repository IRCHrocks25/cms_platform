# Can GoHighLevel forms be created through an API?

**Research date:** 2026-08-13. **Answer:** No supported public GoHighLevel
(HighLevel / LeadConnector) API currently creates, updates, or deletes a native
GHL form definition. The public API can list existing forms, read their
submissions, and upload a file to an existing contact custom field. This is a
statement about the documented public API surface, not proof that no internal
route exists.

## 1. Official API v2 (`services.leadconnectorhq.com`)

HighLevel's current developer portal labels its reference **v3**, while the
installed API examples and existing integrations may still use a `Version`
header such as `2021-07-28`. The relevant public host remains
`services.leadconnectorhq.com`. As of the research date, the official Forms
section lists exactly the operations below: [Forms index](https://marketplace.gohighlevel.com/docs/ghl/forms/forms/),
[scope matrix](https://marketplace.gohighlevel.com/docs/Authorization/Scopes/index.html).

| Endpoint | Method | Purpose | Scope | Supported? |
| --- | --- | --- | --- | --- |
| `/forms/` | `GET` | List existing forms | `forms.readonly` | Yes — [Get Forms](https://marketplace.gohighlevel.com/docs/ghl/forms/get-forms/) |
| `/forms/submissions` | `GET` | Read submissions from existing forms | `forms.readonly` | Yes — [Get Forms Submissions](https://marketplace.gohighlevel.com/docs/ghl/forms/get-forms-submissions/) |
| `/forms/upload-custom-files` | `POST` | Upload permitted file types (up to 50 MB) to a contact custom field; returns the updated contact | `forms.write` | Yes — [Forms index](https://marketplace.gohighlevel.com/docs/ghl/forms/forms/) and [scope matrix](https://marketplace.gohighlevel.com/docs/Authorization/Scopes/) |
| `/forms` | `POST` | Create a native form definition | — | **No: not documented in the official endpoint catalogue** |
| `/forms/:formId` | `PUT`, `PATCH`, or `DELETE` | Modify or delete a native form definition | — | **No: not documented in the official endpoint catalogue** |

This is hard evidence for the exposed operations: the Forms index and the
official scope matrix enumerate them. The conclusion that `POST /forms` and
form update/delete are unavailable is an absence-of-endpoint conclusion from
that catalogue, not a claim that a non-public endpoint cannot exist. The
official [changelog](https://marketplace.gohighlevel.com/docs/Changelog/) has
entries through 2026-07-07 and does not announce a Forms-definition write API.

## 2. Official API v1 (legacy `rest.gohighlevel.com`)

There is no official v1 Forms create/update/delete reference in the preserved
documentation. More importantly, HighLevel says API v1 reached
**end-of-support on 2025-12-31**; existing integrations may continue working,
but v1 receives neither updates nor support. See HighLevel's support article,
last modified 2026-06-19: [HighLevel API Documentation](https://help.gohighlevel.com/support/solutions/articles/48001060529-highlevel-api-documentation).

Therefore v1 is not a viable route for new provisioning. Whether a particular
undocumented legacy `rest.gohighlevel.com` route still responds is **unknown**
and would not constitute supported functionality. There is no documented v1
`POST /forms`, form update, or form delete endpoint to recommend.

## 3. Required scopes and the meaning of `forms.write`

The current official [scope matrix](https://marketplace.gohighlevel.com/docs/Authorization/Scopes/)
assigns `forms.readonly` to `GET /forms/` and `GET /forms/submissions`, and
assigns `forms.write` only to `POST /forms/upload-custom-files`; all three are
Sub-Account access. `forms.write` is documented and is implemented for that
file-upload use case. It is **not** evidence of an unimplemented native-form
CRUD API, nor does it authorize `POST /forms`.

For comparison, the same matrix explicitly lists CRUD routes for resources
that support them, such as [location custom fields](https://marketplace.gohighlevel.com/docs/ghl/locations/custom-field/)
and [contacts](https://marketplace.gohighlevel.com/docs/ghl/contacts/contacts/).
The omission of equivalent Forms routes is material.

## 4. Adjacent primitives and practical substitutes

| Primitive | Officially supported? | What it can do | What it cannot do |
| --- | --- | --- | --- |
| Custom fields | Yes | Create, update, and delete location custom-field definitions via the [Custom Field API](https://marketplace.gohighlevel.com/docs/ghl/locations/custom-field/) with `locations/customFields.write`; use them on contacts and in a manually created form. | It does not create a Form Builder form or place fields onto one. |
| Contacts API | Yes | An externally hosted form can call a trusted server, which creates/upserts a GHL contact and supplies custom-field values using `contacts.write`; the official matrix lists `POST /contacts/`. | It does not produce a native form ID, native form analytics, or a Form Submitted trigger event. |
| Existing workflow | Partly | With a known workflow ID, `POST /contacts/:contactId/workflow/:workflowId` can add a contact to it (`contacts.write`). See the [scope matrix](https://marketplace.gohighlevel.com/docs/Authorization/Scopes/). | The public API exposes `GET /workflows/` under `workflows.readonly`, not workflow-definition CRUD; it does not create a form-triggered workflow. |
| Funnels/websites | Read-only for pages/funnels | Read existing funnel/page records and manage URL redirects. Official [Funnels API](https://marketplace.gohighlevel.com/docs/ghl/funnels/funnels-api/) and [scope matrix](https://marketplace.gohighlevel.com/docs/Authorization/Scopes/) list reads plus redirect CRUD. | No documented funnel/page creation or page-content/form-block authoring API, so it is not a native-form substitute for provisioning. |
| Surveys | Read-only | List existing surveys and their submissions: [Surveys](https://marketplace.gohighlevel.com/docs/ghl/surveys/surveys/) and [Get Surveys](https://marketplace.gohighlevel.com/docs/ghl/surveys/get-surveys/). | No official survey-definition create/update/delete API; it does not bypass the Forms limitation. |
| Snapshots | Limited API support | List snapshots and create a share link: [Snapshots](https://marketplace.gohighlevel.com/docs/ghl/snapshots/snapshots/) and [Create Snapshot Share Link](https://marketplace.gohighlevel.com/docs/ghl/snapshots/create-snapshot-share-link/). A manually curated snapshot may be a UI-level template/provisioning mechanism. | The public API does not document snapshot creation, import, push, or selective cloning of a form into a location, so it cannot automate per-client form provisioning. |
| Native public form widget | Yes for rendering an already-existing form; not a management API | A form can be embedded/rendered at `https://api.leadconnectorhq.com/widget/form/{formId}`; the LeadConnector WordPress plugin documents that public iframe contract. | It requires a pre-existing `formId`; this does not create a form. The cited source is a maintained LeadConnector plugin listing, not the REST API reference: [LeadConnector plugin](https://wordpress.org/plugins/leadconnector/). |
| External form + GHL ingestion | Yes, if implemented with documented Contact/Workflow APIs | Host the UX yourself, submit to your own backend, then create/upsert the contact, attach custom fields/tags, and optionally enroll it in a prebuilt workflow. This is the supported scalable lead-capture path. | It will not behave as a native GHL form unless GHL later exposes a supported native submission/definition API. Do not expose a Private Integration Token in browser code. |

## 5. Unofficial and undocumented routes

Community reverse engineering indicates that the GHL UI and public widgets use
additional internal services, notably `backend.leadconnectorhq.com`. An
official Ideas-board discussion contains a community-reported
`POST https://backend.leadconnectorhq.com/forms/submit` payload pattern,
including a form-data wrapper, but it is a **submission** route, not evidence
of form-definition creation: [API for submitting forms](https://ideas.gohighlevel.com/forms/p/api-for-submitting-forms)
(community post, originally 2023 and updated September 2024). No credentials
were used for this research.

The UI's internal app-session routes, browser automation, and reverse-engineered
requests may be able to create or clone forms because the UI itself must do so.
They are not published contracts: request schemas, authentication/session
mechanics, CSRF controls, and routes can change without versioning. Browser
automation also inherits UI fragility (layout, selectors, MFA, captchas, and
timing) and operational cost. Use of private endpoints may conflict with the
applicable [LeadConnector terms](https://www.leadconnectorhq.com/terms2) and
cannot be treated as API support. This research found no official statement
endorsing either approach; ToS applicability to a particular implementation is
**unknown** and should be reviewed with counsel/account management before any
production use.

A manually operated snapshot import/clone may reduce setup work, but the public
Snapshot API only documents listing and share-link creation. Any automated
snapshot import/push performed through internal routes has the same unsupported
and brittle status.

## 6. Verdict and recommendation

For a product that must provision per-client GHL forms at scale today, do not
design around native Form Builder CRUD: there is no supported public create,
update, or delete API in the documented surface as of 2026-08-13. Prefer an
externally hosted, version-controlled form that posts to your backend; create
or upsert GHL contacts, populate custom fields, tag them, and enroll them in
prebuilt GHL workflows using supported APIs. If native GHL forms are mandatory,
use a human-operated template/snapshot/UI setup process and make it an explicit
operational dependency; treat internal API or browser automation as a
high-maintenance, unsupported exception only after ToS and risk review. Monitor
the official [Forms reference](https://marketplace.gohighlevel.com/docs/ghl/forms/forms/)
and [changelog](https://marketplace.gohighlevel.com/docs/Changelog/) for a
future definition-management endpoint.

## Local integration note (optional context)

The repository's active Business OS integration uses
`https://services.leadconnectorhq.com` with `Version: 2021-07-28` in
[`smm_webapp/smm-frontend/supabase/functions/_shared/ghlApi.ts`](../../smm_webapp/smm-frontend/supabase/functions/_shared/ghlApi.ts)
and a separate dashboard uses the same host in
[`raw/kane-keap-dashboard/supabase/functions/_shared/runGhlSync.ts`](../../raw/kane-keap-dashboard/supabase/functions/_shared/runGhlSync.ts).
Its documented OAuth grant list contains calendar, contact, conversation, and
location scopes but neither `forms.readonly` nor `forms.write`:
[`smm_webapp/smm-frontend/docs/setup/ghl.md`](../../smm_webapp/smm-frontend/docs/setup/ghl.md).
Thus the current integration cannot call even the existing Forms endpoints
without a reauthorization that adds the appropriate scope; adding those scopes
would enable listing/submission reads and custom-field file uploads only, not
native form provisioning.
