---
name: fish-manager
description: Develop, diagnose, test, package, or document the 鱼管家 open-source project. Use for Fish Manager installation failures, 闲管家 order synchronization, business-rule changes, contributor onboarding, and safe Windows releases; do not use for unrelated generic Django projects.
---

# Fish Manager

Work from the repository root containing `pyproject.toml`, `app/`, `tests/`, and `installer/`. If the repository is not available, ask for its path or checkout; do not recreate it from memory.

## Route the task

- For product or code changes, read [references/architecture.md](references/architecture.md).
- For installation, synchronization, release, or security work, also read [references/verification-and-release.md](references/verification-and-release.md).
- For ordinary user questions, prefer the public manual and README. Do not load internal ignored documents or expose local development records.

## Preserve these invariants

- Keep one Windows installer for end users. Edition or capability differences must not create separate installation methods.
- Keep order data, addresses, costs, videos, backups, and credentials local by default. Never print or commit real `XGJ_APP_SECRET`, tokens, supplier URLs, customer data, databases, or logs.
- Treat 闲管家 and other platform responses as external facts. Preserve source payload meaning, idempotency, pagination, retry boundaries, and auditability when changing synchronization.
- Do not fabricate missing platform fields or silently convert unknown money to zero.
- Never send a real shipment, refund, message, or other externally visible operation during testing. Require explicit user authorization immediately before any live business mutation.
- Preserve read and export access to local user data. Do not add artificial lock-in.
- Keep internal planning and development records out of GitHub and release packages.

## Work style

Inspect existing code and tests before changing behavior. Make the smallest coherent change, add or update regression coverage, and report known limitations honestly. Use synthetic data in tests and screenshots.

Run focused tests while iterating. Before claiming a repository-wide change is complete, run the relevant quality gate described in the verification reference. For installer changes, inspect the produced package and verify installation behavior in proportion to the risk.

When publishing documentation, distinguish verified product behavior from plans. State that 鱼管家 is an independent project and not an official product of 闲鱼、鱼小铺、闲管家 or 阿奇索.
