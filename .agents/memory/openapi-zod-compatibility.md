---
name: OpenAPI and Zod compatibility
description: A workspace-specific compatibility constraint for generated Zod schemas.
---

When extending the OpenAPI contract, prefer numeric schemas that the current
Orval/Zod toolchain can represent without top-level helpers unavailable in the
installed Zod version.

**Why:** The generator can emit `zod.int()` for OpenAPI integer fields, while
this workspace currently resolves the Zod v3 API, where that top-level helper
does not exist.

**How to apply:** After every contract change, run codegen and the library
typecheck before depending on generated hooks. If an integer field is not
semantically required, use a numeric schema compatible with the current
generator; otherwise update the toolchain deliberately.