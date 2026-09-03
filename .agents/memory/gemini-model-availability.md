---
name: Gemini model availability
description: How to handle changing Gemini model IDs and separate image-generation quota.
---

Verify model identifiers against the live `google-genai` model listing rather than assuming an older documented ID remains available. Treat text and image generation as separate capability and quota checks.

**Why:** A valid key successfully generated structured text after a retired text model was replaced, while multiple currently listed image models still returned zero image-generation quota.

**How to apply:** When Gemini returns `NOT_FOUND`, inspect the live model list and select a supported equivalent. When image generation returns `RESOURCE_EXHAUSTED`, report it as a key/project quota issue rather than a credential or general Gemini failure.