# 🎨 Oversight Hub - Lo-Fi Sci-Fi Design System

**Version:** 1.0  
**Theme:** Lo-Fi Cyberpunk / Sci-Fi  
**Status:** Active Implementation

---

## 🎯 Design Philosophy

The new Oversight Hub embraces a **lo-fi sci-fi aesthetic** with:

- **Neon colors** (turquoise, purple, pink, cyan)
- **Grid patterns** and digital elements
- **High contrast** for readability
- **Glowing effects** and smooth animations
- **Monospace fonts** for data-heavy sections
- **Mobile-first responsive layout**

---

## 🎨 Color Palette

### Primary Neon Colors

| Color  | CSS Variable    | Hex       | Usage                           |
| ------ | --------------- | --------- | ------------------------------- |
| Cyan   | `--neon-cyan`   | `#00d9ff` | Primary accents, borders        |
| Teal   | `--neon-teal`   | `#00f5b8` | Hover states, highlights        |
| Blue   | `--neon-blue`   | `#0066ff` | Secondary accents               |
| Purple | `--neon-purple` | `#a855f7` | Section titles, secondary focus |
| Pink   | `--neon-pink`   | `#ff006e` | Danger, attention states        |
| Green  | `--neon-green`  | `#00d926` | Success states                  |

### Base Colors

| Element              | Hex       | Usage               |
| -------------------- | --------- | ------------------- |
| Background Primary   | `#0a0e27` | Main app background |
| Background Secondary | `#1a1f3a` | Content containers  |
| Background Tertiary  | `#252d4a` | Cards, panels       |
| Text Primary         | `#e0f7ff` | Main text           |
| Text Secondary       | `#a0d5ff` | Secondary text      |
| Text Tertiary        | `#608a9f` | Labels, hints       |

---

## 📐 Layout Architecture

### Header

- **Height:** 73px
- **Border:** 2px solid cyan (#00d9ff)
- **Effect:** Neon glow text shadow
- **Buttons:** Uppercase, uppercase with glow effect on hover

### Main Panel (Top Section)

- **Flex:** 1 (grows to fill available space)
- **Padding:** 1.5rem 2rem
- **Overflow:** Auto with custom scrollbar
- **Sections:**
  1. Metrics Dashboard (Grid)
  2. Task List (Responsive grid cards)

### Chat Panel (Bottom Section)

- **Height:** 300px (var(--chat-height))
- **Border-top:** 2px solid purple (#a855f7)
- **Sections:**
  - Chat header (1rem padding)
  - Chat content (scrollable)
  - Chat input area

---

## 📊 Component Specifications

### Metric Cards

```
- Grid: auto-fill, minmax(200px, 1fr)
- Gap: 1rem
- Border: 1px solid --accent-primary
- Background: Linear gradient (cyan 0%, purple 100%) with opacity
- Hover: Scale up, enhanced glow, shine animation
- Text: Monospace font for values, uppercase labels
```

### Task Cards

```
- Grid: auto-fill, minmax(300px, 1fr)
- Min Height: 280px
- Border: 1px solid --accent-primary
- Background: Gradient with transparency
- Hover Effects:
  - Border color change to hover state
  - Glow shadow (up to 32px)
  - Transform: translateY(-4px) scale(1.02)
  - Animated border shine
```

### Status Badges

| Status    | Color            | Icon |
| --------- | ---------------- | ---- |
| Completed | Green (#00d926)  | ✓    |
| Running   | Orange (#ffa500) | ⟳    |
| Pending   | Cyan (#00d9ff)   | ⧗    |
| Failed    | Pink (#ff4466)   | ✗    |

---

## ✨ Visual Effects

### Glow Effects

**Shadow Variables:**

```css
--glow-sm: 0 0 8px rgba(0, 217, 255, 0.3);
--glow-md: 0 0 16px rgba(0, 217, 255, 0.5);
--glow-lg: 0 0 24px rgba(168, 85, 247, 0.6);
```

### Animations

**Neon Flicker (Header Title)**

```css
@keyframes neon-flicker {
  0%,
  100% {
    text-shadow: 0 0 12px var(--accent-primary);
  }
  50% {
    text-shadow:
      0 0 20px var(--accent-primary),
      0 0 8px var(--accent-secondary);
  }
}
```

**Progress Bar**

```css
@keyframes loading {
  0% {
    width: 0%;
  }
  50% {
    width: 80%;
  }
  100% {
    width: 100%;
  }
}
```

**Pulse (Running Status)**

```css
@keyframes pulse {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.7;
  }
}
```

### Transitions

- Default: 0.3s ease
- Button hover: 0.2s
- Glow effects: 0.5s

---

## 📱 Responsive Breakpoints

| Breakpoint | Width      | Grid Cols      | Notes            |
| ---------- | ---------- | -------------- | ---------------- |
| Desktop    | 1200px+    | 3-4 task cards | Full experience  |
| Tablet     | 768-1200px | 2 task cards   | Optimized layout |
| Mobile     | <768px     | 1 task card    | Full-width stack |

---

## 🎛️ Typography

### Font Stack

```css
-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif
```

### Font Sizes

| Usage            | Size    | Weight | Color              |
| ---------------- | ------- | ------ | ------------------ |
| Header Title     | 1.75rem | 700    | --accent-primary   |
| Section Title    | 1.25rem | 700    | --accent-secondary |
| Card Title       | 1.1rem  | 700    | --accent-secondary |
| Body Text        | 0.95rem | 400    | --text-primary     |
| Labels           | 0.75rem | 600    | --text-tertiary    |
| Monospace Values | 0.85rem | 400    | --accent-primary   |

---

## 🎯 Interactive States

### Buttons

```
Normal → Hover → Active → Focus
- Hover: Background brightens, glow increases, transform up
- Active: Transform reduced, glow maintained
- Focus: Border highlighted, glow ring visible
```

### Cards

```
Normal → Hover → Active
- Hover:
  - Scale: 1 → 1.02
  - Translate: 0 → -4px
  - Glow: light → medium + heavy
  - Border: primary → hover color
- Active:
  - Scale: 1.02 → 1.01
  - Translate: -4px → -2px
```

### Inputs

```
Normal → Focus → Error
- Focus:
  - Border color: secondary → primary
  - Glow: small → medium
  - Shadow: light → medium
- Error:
  - Border: var(--error-border)
  - Background: var(--error-bg)
  - Shadow: red glow
```

---

## 🔄 Theme Customization

### CSS Variables Available

```css
/* Neon Colors */
--neon-cyan
--neon-teal
--neon-blue
--neon-purple
--neon-pink
--neon-green

/* Base Theme */
--bg-primary
--bg-secondary
--bg-tertiary
--text-primary
--text-secondary
--text-tertiary

/* Accents */
--accent-primary
--accent-secondary
--accent-tertiary
--accent-success
--accent-warning
--accent-danger

/* Effects */
--glow-sm
--glow-md
--glow-lg
```

To create alternate themes, override these variables:

```css
:root[data-theme='dark'] {
  /* Override colors here */
}

:root[data-theme='custom'] {
  /* Custom theme */
}
```

---

## 📋 Implementation Checklist

- [x] Color palette defined
- [x] Layout structure created
- [x] Header styled with glow effects
- [x] Metric cards with grid layout
- [x] Task cards with hover animations
- [x] Chat panel at bottom
- [x] Responsive grid system
- [ ] Task detail modal updated (lo-fi theme)
- [ ] Chat message styling enhanced
- [ ] Additional animations (micro-interactions)
- [ ] Accessibility review
- [ ] Performance optimization
- [ ] Cross-browser testing

---

## 🚀 Next Steps

1. **Task Detail Modal:** Update to match neon theme
2. **Chat Styling:** Add gradient backgrounds for messages
3. **Animations:** Add more micro-interactions
4. **Accessibility:** Ensure proper contrast ratios
5. **Performance:** Optimize animations for mobile
6. **Testing:** Cross-browser and device testing

---

## 📚 Design Resources

- **Font:** System fonts (no external loading)
- **Icons:** Unicode symbols + emojis
- **Effects:** CSS animations and transitions
- **Patterns:** Repeating grid background
- **Inspiration:** Cyberpunk 2077, Synthwave, Vaporwave aesthetics
