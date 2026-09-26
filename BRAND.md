# Prop Desk brand assets

The mark is an equity curve stepping up over its trailing floor. That is the one relationship the whole app exists to track, so it is the logo.

## Colors

| Token | Hex | Use |
|---|---|---|
| `--accent` | `#06b6d4` | the curve. The only saturated color in the UI. Reserve it for the number that matters on each card |
| `--floor` | `#64748b` | the floor line, muted UI text, borders |
| `--ink` | `#e8edf5` | text on dark |
| `--ink-dark` | `#0f172a` | text on light |
| `--bg` | `#0b0e13` | page background |
| `--surface` | `#131922` | cards |
| `--border` | `#222c3a` | card borders |

`#06b6d4` was picked over a brighter cyan because it holds contrast on a white browser tab as well as on the dark UI. One mark, both places, no light-mode variant needed.

## Files

| File | Use |
|---|---|
| `logo-mark.svg` | primary mark, transparent, scales to anything |
| `logo-mark-mono.svg` | single color, inherits `currentColor`. For buttons, print, anywhere the two-tone version won't work |
| `logo-lockup-dark.svg` | mark + wordmark for dark backgrounds. Nav bar, login screen |
| `logo-lockup-light.svg` | same for light backgrounds. Invoices, exports |
| `favicon.ico` | multi-resolution, 16 through 256 |
| `logo-mark-512.png` … `-16.png` | raster fallbacks and manifest icons |
| `logo-mark-180.png` | apple-touch-icon |
| `logo-mark-192.png` / `-512.png` | PWA manifest |

## HTML head

Put the files in `/static/brand/` and add:

```html
<link rel="icon" href="/static/brand/favicon.ico" sizes="any">
<link rel="icon" href="/static/brand/logo-mark.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="/static/brand/logo-mark-180.png">
<link rel="manifest" href="/static/site.webmanifest">
<meta name="theme-color" content="#0b0e13">
```

The SVG icon is what modern browsers actually use. The `.ico` is the fallback, and it needs `sizes="any"` or Chrome will prefer it over the SVG.

## site.webmanifest

```json
{
  "name": "Prop Desk",
  "short_name": "Prop Desk",
  "icons": [
    { "src": "/static/brand/logo-mark-192.png", "sizes": "192x192", "type": "image/png" },
    { "src": "/static/brand/logo-mark-512.png", "sizes": "512x512", "type": "image/png" }
  ],
  "theme_color": "#0b0e13",
  "background_color": "#0b0e13",
  "display": "standalone"
}
```

## Inline in a template

The mark is small enough to inline, which avoids a request and lets it inherit theme colors:

```html
<svg viewBox="0 0 63.5 63.5" width="28" height="28" aria-label="Prop Desk">
  <g transform="translate(-1.25 -0.375)" fill="none"
     stroke-linecap="round" stroke-linejoin="round">
    <path d="M10 53 H50" stroke="#64748b" stroke-width="6"/>
    <path d="M10 41 H26 V28 H42 V13 H56" stroke="#06b6d4" stroke-width="9.5"/>
  </g>
</svg>
```

## Rules

- **Clear space**: keep one step-height of empty space on all sides. Nothing crowds the mark
- **Minimum size**: 16px for the mark. Below that the floor line merges into the curve. The lockup has a minimum of 120px wide
- **Do not** recolor the curve to anything but the accent, add a background plate, add a gradient, outline it, rotate it, or stretch either axis
- **Do not** use the lockup where the wordmark would render under about 11px. Use the mark alone instead
- The floor line is always dimmer than the curve. That contrast is the whole idea: the bright thing is your balance, the dim thing is the line it must stay above
