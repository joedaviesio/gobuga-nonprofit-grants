# Bowen Style Guide

Portable design system reference. Plug this into any new project to maintain brand consistency.

---

## Fonts

### Primary — Clother (Adobe Typekit) np

Used across the entire app at different weights. Same family, very different feel.

- **Source:** [Adobe Fonts (Typekit)](https://use.typekit.net/czn0xnx.css)
- **Import:**
  ```html
  <link rel="stylesheet" href="https://use.typekit.net/czn0xnx.css" />
  ```
- **CSS:**
  ```css
  font-family: 'clother', sans-serif;
  ```
- **Weights used:** 300, 400, 500, 600, 700

#### Clother Weight Usage

| Context              | Weight | Style  | Class / Rule         | Visual Feel          |
|----------------------|--------|--------|----------------------|----------------------|
| Body / message text  | 300    | normal | Global body default  | Light, readable      |
| Labels               | 400    | normal | `font-normal`        | Regular              |
| UI elements          | 500    | normal | `font-medium`        | Medium emphasis      |
| Headings / strong    | 600    | normal | `font-semibold`      | Semibold emphasis    |
| Brand lockup "Bowen" | 700    | italic | `.bowen-brand`       | Bold italic, punchy  |

#### Brand Lockup vs Body Text

The brand logo and message bubbles both use Clother — the difference is weight and style:

```css
/* Message bubbles / body text — thin and clean */
body {
  font-family: 'clother', 'Inter', system-ui, -apple-system, sans-serif;
  font-weight: 300;
  font-style: normal;
}

/* Brand lockup "Bowen" — heavy and italic */
.bowen-brand {
  font-family: 'clother', sans-serif;
  font-weight: 700;
  font-style: italic;
}
```

### Body — Inter (Google Fonts)

Primary body and UI typeface.

- **Source:** [Google Fonts — Inter](https://fonts.google.com/specimen/Inter)
- **Import:**
  ```css
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
  ```
- **Weights used:** 400, 500, 600, 700

### Secondary — Open Sans (Google Fonts)

Used for textareas and long-form input.

- **Source:** [Google Fonts — Open Sans](https://fonts.google.com/specimen/Open+Sans)
- **Import:**
  ```css
  @import url('https://fonts.googleapis.com/css2?family=Open+Sans:wght@400;500;600&display=swap');
  ```
- **Weights used:** 400, 500, 600

### Font Stack

```css
--font-sans: 'clother', 'Inter', system-ui, -apple-system, sans-serif;
--font-serif: Georgia, serif;
```

### Global Font Defaults

```css
body {
  font-weight: 300;
  font-style: normal;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}
```

---

## Colors

### Brand Colors

| Name   | Hex       | Usage                        |
|--------|-----------|------------------------------|
| Blue   | `#5d78c5` | Brand letter coloring (B)    |
| Yellow | `#ffce31` | Brand letter coloring (o, e) |
| Red    | `#e25063` | Brand letter coloring (w, n) |

These three colors rotate across the "Bowen" wordmark and are used for the chat input border gradient.

### Core Palette

| Token           | Value     | Usage              |
|-----------------|-----------|--------------------|
| `--primary`     | `#000000` | Text, buttons, UI  |
| `--primary-light` | `#333333` | Secondary text   |

### Neutrals (Slate)

| Class       | Hex       | Usage                          |
|-------------|-----------|--------------------------------|
| slate-50    | `#f8fafc` | Page backgrounds, card fills   |
| slate-100   | `#f1f5f9` | Hover states, gallery bg       |
| slate-200   | `#e2e8f0` | Borders, dividers              |
| slate-300   | `#cbd5e1` | Disabled borders               |
| slate-400   | `#94a3b8` | Placeholder text               |
| slate-500   | `#64748b` | Muted text                     |
| slate-600   | `#475569` | Secondary text                 |
| slate-700   | `#334155` | Body text                      |
| slate-800   | `#1e293b` | Heading text                   |
| slate-900   | `#0f172a` | Primary text                   |

### Semantic Colors

| Context  | Background | Border       | Text        |
|----------|------------|--------------|-------------|
| Success  | green-50   | green-200    | green-700   |
| Warning  | amber-50   | amber-200    | amber-800   |
| Error    | red-50     | red-200      | red-700     |
| Info     | blue-600   | —            | white       |

### Gradients

```css
/* User message bubble np*/
background: linear-gradient(to bottom right, #DC602E, #2d2d44);

/* Input area fade-out */
background: linear-gradient(to top, white, white, transparent);
```

---

## Typography Scale

| Element          | Size        | Weight | Notes                    |
|------------------|-------------|--------|--------------------------|
| Page heading     | text-3xl    | 700    |                          |
| Section heading  | text-2xl    | 600    |                          |
| Component title  | text-xl     | 600    |                          |
| Body             | text-base   | 300    | Default                  |
| Label            | text-sm     | 500    |                          |
| Micro label      | text-xs     | 500    | tracking-wide, uppercase |
| Tiny label       | 10px        | 500    | tracking-wider           |
| Input text       | 15px        | 400    | Open Sans                |

### Prose / Markdown Content

```css
.prose p         { margin: 0.5em 0; }
.prose ul, ol    { margin: 0.5em 0; padding-left: 1.5em; }
.prose li        { margin: 0.25em 0; }
.prose strong    { font-weight: 600; }
.prose code      { background: #FAF9F6; padding: 0.125em 0.25em; font-size: 0.875em; border-radius: 0.25em; }
```

---

## Spacing

Follows Tailwind's default 4px base unit.

| Token | Value  | Common usage                   |
|-------|--------|--------------------------------|
| 1     | 4px    | Tight gaps                     |
| 2     | 8px    | Icon gaps, small padding       |
| 3     | 12px   | Button padding, flex gaps      |
| 4     | 16px   | Card padding, section margins  |
| 6     | 24px   | Medium section spacing         |
| 8     | 32px   | Large section spacing          |
| 12    | 48px   | Page section padding (py-12)   |

---

## Border Radius

| Token       | Value | Usage                       |
|-------------|-------|-----------------------------|
| rounded-sm  | 2px   | Message bubble corners      |
| rounded-md  | 6px   | Icon backgrounds            |
| rounded-lg  | 8px   | Cards, inputs               |
| rounded-xl  | 12px  | Primary components          |
| rounded-2xl | 16px  | Message bubbles             |
| rounded-full| 9999px| Status indicators, avatars  |

---

## Shadows

```css
/* Standard card / header */
box-shadow: shadow-lg;

/* Buttons */
box-shadow: shadow-md;

/* Modals */
box-shadow: shadow-2xl;

/* Chat input */
box-shadow: shadow-lg shadow-slate-200/50;
```

---

## Borders

| Style                | Usage                      |
|----------------------|----------------------------|
| 1px slate-200        | Default dividers           |
| 1px slate-100        | Subtle dividers            |
| 2px brand colors     | Chat input frame (rotating)|
| 4px left accent      | Blockquotes, callouts      |
| white/20             | Overlays on dark bg        |

### Chat Input Border

The chat input uses a 2px border with brand colors randomly assigned to each side:

```
border-color: top #5d78c5, right #ffce31, bottom #e25063, left #5d78c5
```

Colors shuffle from: `#5d78c5`, `#ffce31`, `#e25063`.

---

## Interactive States

### Focus

```css
input:focus, textarea:focus, button:focus {
  outline: none;
}
input:focus-visible, textarea:focus-visible, button:focus-visible {
  outline: 2px solid var(--primary);
  outline-offset: 2px;
}
```

### Hover

- Links: `opacity-80`
- Text links: `text-slate-600`
- Backgrounds: `bg-slate-100`
- Buttons: `gradient-to-r from-primary to-primary-light`

### Disabled

- Cursor: `not-allowed`
- Text: `text-slate-300`
- Background: `bg-slate-100`

### Transitions

- Colors: `transition-colors` (150ms)
- Opacity: `transition-opacity` (150ms)
- All: `transition-all` (300ms)

---

## Scrollbar

```css
::-webkit-scrollbar {
  width: 6px;
  height: 6px;
}
::-webkit-scrollbar-track {
  background: transparent;
}
::-webkit-scrollbar-thumb {
  background: rgba(0, 0, 0, 0.2);
  border-radius: 3px;
}
::-webkit-scrollbar-thumb:hover {
  background: rgba(0, 0, 0, 0.3);
}
```

---

## Z-Index Layers

| Layer  | Value | Usage          |
|--------|-------|----------------|
| Header | z-40  | Sticky nav     |
| Modal  | z-50  | Overlays       |

---

## Responsive Breakpoints

Standard Tailwind breakpoints:

| Prefix | Min-width |
|--------|-----------|
| sm     | 640px     |
| md     | 768px     |
| lg     | 1024px    |

### Common Patterns

```
grid-cols-1 sm:grid-cols-2 lg:grid-cols-4   /* Card grids */
hidden sm:inline                              /* Desktop-only text */
max-w-4xl mx-auto                             /* Content container */
```

---

## Quick Start for a New Project

1. Add the font imports to your `<head>` or CSS:
   ```html
   <link rel="stylesheet" href="https://use.typekit.net/czn0xnx.css" />
   ```
   ```css
   @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
   @import url('https://fonts.googleapis.com/css2?family=Open+Sans:wght@400;500;600&display=swap');
   ```

2. Set the font stack in your Tailwind config or CSS:
   ```js
   fontFamily: {
     sans: ['clother', 'Inter', 'system-ui', '-apple-system', 'sans-serif'],
   }
   ```

3. Add brand colors to your Tailwind config:
   ```js
   colors: {
     primary: { DEFAULT: '#000000', light: '#333333' },
     brand: {
       blue: '#5d78c5',
       yellow: '#ffce31',
       red: '#e25063',
     }
   }
   ```

4. Apply global body styles (weight 300, antialiased).
