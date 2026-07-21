# ADR 0002: Separate EFMI Authoring and Export Zones

- Status: Superseded by ADR 0003
- Date: 2026-07-21
- Supersedes: ADR 0001's Part Batch output lifecycle; its partition projection and boundary-weight rules remain accepted.

## Context

Authors need complete bodies and garments for weight painting, INI toggles, and CrossIB setup. Editing generated Component outputs creates two competing sources of truth, while append and replace batches can overwrite unrelated outputs or leave stale geometry exportable.

## Decision

Velo keeps complete meshes in one Authoring Zone and treats the Export Zone as a read-only Sync Snapshot. Synchronization rebuilds every generated Component output in a staging collection, validates the complete result, and replaces the previous Export Zone only after all checks pass.

The only structural difference between zones is Component partitioning. Mesh data, weights, ShapeKeys, materials, normals, transforms, constraints, modifiers that preserve topology, custom properties, INI toggle bindings, and CrossIB bindings derive from Authoring Zone objects. Explicit Passthrough Mappings place unchanged objects into selected Components.

The EFMI exporter reads only the Export Zone. It rejects export when the skeleton mode is not `MERGED`, the configured export collection is not the current Export Zone, or the Authoring Zone manifest no longer matches the Sync Snapshot.

## Consequences

- Authors never edit generated Component outputs or manage `part.N` batches.
- Failed synchronization leaves the last valid Export Zone intact.
- Changes in the Authoring Zone require synchronization before export.
- Generated output visibility is an authoring convenience and does not determine export eligibility.
- Topology-changing modifiers must be applied before synchronization.
