# Project architecture and business invariants

Read this reference when changing Fish Manager product behavior or code.

## Product boundary

Fish Manager is a local-first Django back office for real-goods sellers using 闲鱼 / 鱼小铺. It concentrates on order synchronization, text shipping lists, cost and profit, and refund recovery. It is not a replacement client for 闲鱼 and must not bypass platform rules.

## Main code areas

- `app/integrations/`: 闲管家 client, connection state, platform facts, synchronization runs and external revisions.
- `app/workbench/`: seller-facing projections, historical import, profit/refund views and supplier text exports.
- `app/orders/`: sales order lifecycle, shipment, cancellation and return behavior.
- `app/procurement/` and `app/inventory/`: supplier purchasing, allocation, receipts and stock.
- `app/finance/`: money entries and financial projections.
- `app/evidence/`: order evidence metadata and local file access.
- `app/accounts/`, `app/audit/`: local ownership, access control and audit records.
- `app/config/settings/`: separate development, test, local release and Windows release settings.
- `scripts/`: quality, backup/restore, release review and installer automation.
- `installer/fish-manager.iss`: the single Windows installer definition.

Follow model relationships and existing service functions rather than writing directly to tables from views. Database changes require migrations. Keep views thin enough that business rules can be tested without a browser.

## Synchronization model

Platform orders are external facts before they become workbench projections. Preserve the platform order identifier and update cursor. Historical import must paginate until the documented boundary, remain idempotent across retries, and surface incomplete windows or limits instead of claiming completeness.

When diagnosing missing orders, distinguish:

1. authorization or shop selection failure;
2. upstream time-window or quota limits;
3. pagination/cursor failure;
4. mapping or status normalization failure;
5. projection/rebuild failure;
6. UI filtering or stale display.

Do not fix a downstream symptom by duplicating records. Add a fixture or mocked response covering the exact boundary.

## Money and status rules

Money values are stored as integer minor units where the surrounding model follows that convention. Missing cost is unknown, not zero. Estimated profit and realized profit must remain distinguishable. Refund status and supplier-fund recovery are separate facts.

Platform statuses can evolve. Preserve raw or source facts needed to re-project later, and make unknown states visible for review rather than silently coercing them into a convenient state.

## Supplier lists and legacy data

The public release only generates text shipping lists for the owner to copy or download. It does not generate supplier access links, accept waybills or videos, run a public gateway, or submit platform shipment from a supplier flow.

Legacy access, dispatch and video rows remain in the schema so upgrades do not destroy user data. Existing videos stay outside public static files and remain available only through an authenticated owner download. Do not include local media in release artifacts.

## UI changes

Use the established tokens and shared components in `app/static/tokens.css`, `theme.css`, and workbench styles. Preserve clear Chinese labels, keyboard access, error recovery and accurate empty states. Screenshots and fixtures must contain synthetic data only.
