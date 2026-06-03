---
name: codekg-add-feature
description: Guides where and how to implement a new feature using CodeKG. Finds the right layer structure, similar existing patterns, contracts to implement, and files to create or modify — without writing any code.
argument-hint: "<feature to add, e.g. add vacancy application, add role-based auth>"
allowed-tools: mcp_code-kg_find_nodes, mcp_code-kg_get_node, mcp_code-kg_semantic_search, mcp_code-kg_list_layers, mcp_code-kg_impact_analysis, mcp_code-kg_trace_call_chain
---

# CodeKG: Where and How to Add a New Feature

Use when the user asks "where should I add X", "how do I implement X in this codebase", "what pattern should I follow for X", or "which files do I need to create for X".

## Hard rules

1. CodeKG read tools only. Never call write tools.
2. Maximum 8 CodeKG tool calls.
3. Ground every recommendation in an actual node from the graph — do not invent file paths or class names.
4. Always find a similar existing feature to use as the reference pattern. Never give generic advice disconnected from what is already in the codebase.

## Procedure

### Step 1 — Understand the architecture (1 call)

Call `list_layers` for the target repo.
Identify which layers are present. Determine which layers the new feature will need
(typically controller + service + repository for a CRUD feature, or middleware + service for auth).

### Step 2 — Find the most similar existing feature (1–2 calls)

Call `semantic_search`:
- query: `$1`
- `scope: "code"`
- `top_k: 8`

Pick the closest match — the existing feature whose domain most resembles `$1`.
This becomes the **reference pattern** for Step 3.

If semantic search returns low-confidence results (no summaries / graph not enriched):
  Fall back to `find_nodes` with key nouns from `$1` and `use_semantic: false`.

### Step 3 — Trace the reference pattern (2 calls)

On the reference feature's main service class, call `get_node` with `include_neighbors: true`.
On the reference feature's controller class, call `get_node` with `include_neighbors: true`.

Extract from these two calls:
- The interface the controller depends on (service contract)
- The interface the service depends on (repository contract)
- The DTO classes used as input/output
- The base classes inherited from (e.g. `BaseRepository<T>`)
- File path conventions (namespaces, directory structure)

### Step 4 — Identify contracts to implement (1 call)

Call `find_nodes`:
- query: `"I" + primary_entity_name + "Service" OR "I" + primary_entity_name + "Repository"`
  (e.g. `ICandidateService`, `ICandidateRepository`)
- `types: ["Interface"]`
- `use_semantic: false`
- `limit: 5`

If these interfaces already exist (partial implementation of the feature), surface them.
If they don't exist, note that they need to be created following the reference pattern.

### Step 5 — Map files to create or modify (no extra calls needed)

Using the reference pattern nodes gathered in Steps 2–4, derive:
- **New files to create**: mirror the reference pattern's file structure for the new entity
- **Existing files to modify**: the DI registration file, DbContext, route config
- **Interfaces to implement**: service + repository contracts

Identify the DI/startup file by looking for `config` or `middleware` layer nodes
that contain "ServiceCollection", "Configure", "Startup", or "Program" in their name.

## Output

```
## Implementation Guide: [feature name]

### Reference Pattern
Closest existing feature: [feature name]
Files to mirror:
- [ExistingController.cs] → create [NewController.cs]
- [ExistingService.cs] → create [NewService.cs]
- [IExistingService.cs] → create [INewService.cs]
- [ExistingRepository.cs] → create [NewRepository.cs]
- [IExistingRepository.cs] → create [INewRepository.cs]
- [ExistingDTO.cs] → create [NewInputDTO.cs], [NewOutputDTO.cs]
- [ExistingEntity.cs] → create [NewEntity.cs]

### Layer Structure for [feature]
| Layer | File to create | Inherits / Implements |
|---|---|---|
| Controller | [path] | — |
| Service | [path] | [INewService] |
| Repository | [path] | [BaseRepository<NewEntity>] |
| Model/Entity | [path] | — |
| DTO | [path] | — |

### Contracts to implement
**Service interface:** [IExistingService signature — adapt method names]
**Repository interface:** [IExistingRepository signature — adapt entity type]

### Files to modify (not create)
- [Program.cs / Startup.cs]: register INewService → NewService and INewRepository → NewRepository
- [ApplicationDbContext.cs]: add DbSet<NewEntity>
- [any migration / schema file if applicable]

### Conventions observed from reference pattern
[2–4 bullet points: naming, namespace structure, DTO suffix patterns, etc.]

### Recommended implementation order
1. Entity / model
2. DTOs (input + output)
3. Repository interface + implementation
4. Service interface + implementation
5. Controller action
6. DI registration
7. Tests mirroring [ReferenceServiceTest.cs]
```
