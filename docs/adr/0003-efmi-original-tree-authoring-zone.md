# ADR 0003: Use the Imported EFMI Tree as the Authoring Zone

- Status: Accepted
- Date: 2026-07-21
- Supersedes: ADR 0002's separate Authoring Collection lifecycle. ADR 0002's atomic Export Zone and export-freshness rules remain accepted.

## Context

EFMI authors already organize editable meshes under the imported Character `C0` through `C15` tree. Moving complete bodies and garments into a second Authoring Collection obscures their home Component, duplicates collection state, and makes existing INI Toggle and CrossIB workflows harder to understand.

## Decision

The imported Character Component tree is the Authoring Zone. Bodies and garments become Whole Meshes only through explicit registration and are moved to their selected home Component without being copied. The first registered body is the Partition Standard Body; all later Whole Meshes derive their partition labels from it.

Synchronization atomically rebuilds a sibling `[分割区]` Export Zone. Original imported meshes are copied one-to-one unless they form the standard-body source reference. Whole Meshes are split by the standard body. Block Reassignment, INI Toggle, and CrossIB bindings expand only in the Export Zone.

## Consequences

- Authors edit the original Character tree and never manage a second authoring hierarchy.
- A Whole Mesh home Component does not alter its generated partition routes.
- Hidden authoring objects are omitted from the next snapshot; a hidden standard body still partitions other Whole Meshes.
- Old `part.N` outputs are disposable migration artifacts and are removed only after a successful new snapshot.
