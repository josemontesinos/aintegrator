# 001 — Project and package name

- Status: accepted
- Date: 2026-11-22
- Phase: 0

## Context

The spec uses the working name `aintegrator` and asks for a final name before phase 0 ends, with
the Python package name kept in sync. A stable name is needed for distribution names, import
namespaces, the CLI entry point and container/ECS naming.

## Decision

Keep **AIntegrator**. Distribution names use `aintegrator-<component>` (for example
`aintegrator-integration`); import packages use `aintegrator_<component>`; the CLI is
`aintegrator`.

## Alternatives considered

- A more memorable marketing name — rejected: the spec's working name is already clear, and
  renaming every workspace member later adds churn with no technical benefit.
- A single `aintegrator` package with subpackages — rejected: the spec requires one pyproject and
  one test suite per top-level package, which a uv workspace models directly.

## Consequences

Every package name is predictable, and import-linter can address packages by a stable module
name. Renaming later would require touching all pyprojects, imports and CI configuration.
