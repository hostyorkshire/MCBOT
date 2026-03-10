**Design update from user:** Please make the system stats display compact and pretty, positioned at the **top of the page** — ideally as a slim horizontal "stats bar" or inline row just below the header / refresh bar, rather than a full-sized card between the Bot Status and Active Stories sections.

Suggested approach:
- Replace the full `.card` section with a compact single-row stats strip (e.g. a `<div class="sysinfo-bar">`) that sits just below the refresh bar at the top of the page.
- Display all four stats inline in one row: 🌡 Temp · 🖥 CPU · 🧠 RAM · 💾 Disk — separated by dividers or subtle spacing.
- Keep the values concise: e.g. `47°C`, `12%`, `230/416 MB (55%)`, `2.9/28 GB (11%)`.
- Style it to be visually distinct but not dominant — a muted background, small font, tight padding. Think of it like a system status ribbon rather than a big card.
- Add CSS for `.sysinfo-bar` to `dashboard/static/style.css` to achieve this look.
- Temperature should turn amber/orange if ≥ 70°C and red if ≥ 80°C (Pi Zero thermal throttle zone).
- Keep all the existing JS wiring (Socket.IO + polling fallback) from the original spec.