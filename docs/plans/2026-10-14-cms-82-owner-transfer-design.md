# CMS-82 two-owner removal design

## Goal

Let an agency operator remove the site's canonical owner when another active Owner member can take over, while preserving a valid owner and access at every committed state.

## Investigation

`Tenant.owner` is a required, protected foreign key. `TenantMembership.role` can be Owner for several members. Existing agency and site-team remove routes reject deletion of the canonical owner's membership; the agency site menu still offers a Remove action that ends in an error. Removing a different Owner-role membership already works in the backend. The installed customer account and exact acting user are not identified, so live reproduction remains required.

## Options considered

1. **Explicit agency “Transfer and remove” action (chosen).** When exactly two active Owner memberships exist, show the replacement's name on the canonical owner's row. A dedicated POST checks the replacement and atomically changes `Tenant.owner` and deletes the former owner's membership. The normal Remove action remains for the secondary Owner. The site Team page still refuses self-removal and canonical-owner removal.
2. Two separate steps, transfer ownership then remove the former owner. This creates a committed interval with an extra Owner and invites operators to stop halfway.
3. Make the ordinary Remove action silently transfer to any available Owner. With more than two, the successor is ambiguous; even with two, the ownership change needs clear confirmation.

## Safety and data flow

The service locks the tenant and its memberships, then rechecks the target is the canonical Owner, exactly one other active Owner member exists, and that member matches the submitted successor. Both changes run in one database transaction. Missing, inactive, extra, or changed memberships fail without a partial write. The agency-only endpoint provides CSRF protection and a named confirmation; it redirects with a clear success or error message. The canonical owner's role cannot be downgraded through the agency role endpoint before transfer. The user account itself is not deleted; other-site access remains intact.

## Acceptance checks

Automated tests cover two-owner transfer/removal, ordinary removal of the secondary Owner, one-owner and more-than-two-owner refusal, inactive successor, stale or forged successor, unauthorized actors, canonical role downgrade refusal, site Team self-removal protection, and transaction rollback. Browser checks verify the agency menu and confirmation with two distinct logins. Installed-account acceptance must identify the affected site and perform the requested real two-to-one transition; tests alone cannot satisfy that check.
