# ADR 0001: Start with a modular monolith

## Decision

Keep Trip, Wishlist, Planning, Events and Memory as modules in one Python core package. Run long-lived work in a separate worker process, but do not split agents into services.

## Rationale

The domain and transaction boundaries are still evolving. This keeps local development and refactoring fast while preserving ports and adapters for later extraction.

