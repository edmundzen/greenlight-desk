---
name: Opening generated SVG art
description: Browser-compatible handling for opening generated SVG key art in a separate tab.
---

Open generated SVG key art through a temporary browser Blob URL rather than navigating directly to its base64 data URL.

**Why:** Microsoft Edge displayed the raw `data:image/svg+xml;base64` navigation as a blank page even though the same SVG rendered inside the app. The user confirmed that the Blob URL approach renders correctly.

**How to apply:** Keep data URLs for embedded image sources if convenient, but convert them to a typed SVG Blob before opening a separate viewing tab and revoke the object URL after a delay.