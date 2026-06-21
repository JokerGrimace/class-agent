# iClass API Route Hiding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove all iClass internal API path and request-field literals from public source and load them from `OPENCLAW_ICLASS_API_ROUTES`.

**Architecture:** Add one typed nested operation mapping to the existing Pydantic settings model. `IClassApiClient` resolves paths and outgoing field names through validating helpers while retaining all existing public method signatures and alias behavior.

**Tech Stack:** Python, Pydantic Settings, unittest, AST-based source checks

---

### Task 1: Lock the configuration contract

**Files:**
- Modify: `tests/test_config_security.py`
- Modify: `app/config.py`

- [x] Add a failing AST test that requires `iclass_api_routes` to default to an empty dictionary.
- [x] Run `python tests/test_config_security.py` and confirm failure because the field is absent.
- [x] Add `iclass_api_routes: dict[str, dict[str, Any]] = {}` next to the existing iClass settings.
- [x] Add a test proving nested JSON loads from `OPENCLAW_ICLASS_API_ROUTES`.
- [x] Run the configuration tests and confirm they pass.

### Task 2: Hide route literals

**Files:**
- Create: `tests/test_iclass_route_security.py`
- Modify: `app/core/iclass/client.py`

- [x] Add tests using the exact operation-key set from the design.
- [x] Add source tests rejecting old route and request-field literals.
- [x] Run the tests and confirm failure because literals remain and helpers are absent.
- [x] Add `_route(operation)` and `_field(operation, field)` validation.
- [x] Replace every direct route and outgoing request-field literal with configuration lookups.
- [x] Run both security test files and confirm all tests pass.

### Task 3: Verify public-source and Git hygiene

**Files:**
- Verify: repository worktree and Git object database

- [x] Scan the public worktree for common credential formats.
- [x] Confirm `app/core/iclass/client.py` contains no internal API path or request-field literals.
- [x] Confirm `OPENCLAW_ICLASS_API_ROUTES` is not accompanied by a committed real-value example.
- [x] Scan all reachable Git blobs for credential formats.
- [x] Record the verification result in `project_process/progress.md`.
