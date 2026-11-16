---
name: betterdisplay-docs-and-lg5k-identity
description: Where BetterDisplay docs live now, how to fetch them from the sandbox, and how the LG 5K displays identify themselves (product ID varies by port)
metadata:
  type: reference
---

BetterDisplay docs moved off the GitHub wiki on 2026-11-13. The wiki pages are stubs pointing at https://betterdisplay.pro/guide/ (CLI reference at /guide/integration/cli-reference/, sitemap at /sitemap.xml).

- Bash curl to betterdisplay.pro is denied by the sandbox proxy. `ctx_index({url})` then `ctx_search({queries, source: url})` works.
- Pre-retirement wiki text is still in git: clone https://github.com/waydabber/BetterDisplay.wiki.git and read commit ba7f4db (2026-11-05).
- The BetterDisplay CLI needs the app running. Do not run the bundle binary to get help when the app is closed; grep `strings` output of the binary instead.

LG ULTRAGEAR+ 5K identity (checked 2026-11-16 with CGDisplayVendorNumber/ModelNumber): vendor 0x1e6d, product ID 0x5cf7, 0x5cf8 or 0x5cf9 depending on the input or port. BetterDisplay therefore tracks the two monitors as several tagIDs, three of them named "LG ULTRAGEAR+ 1". Anything keyed on product ID or tagID (override plists, Configuration Protection) has to cover every variant.

Related: the user's display arrangement notes in the project auto-memory (display-arrangement-drift).
