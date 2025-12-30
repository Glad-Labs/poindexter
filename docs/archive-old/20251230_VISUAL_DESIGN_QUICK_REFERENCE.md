# 🎨 Visual Design Quick Reference

**Complete visual transformation applied to Glad Labs public site**

---

## 📸 Visual Component Guide

### Header Component
- **Location**: `components/Header.js`
- **Pattern**: Modern glassmorphism with scroll detection
- **Features**: Gradient logo, animated nav underlines, CTA button
- **Classes**: Fixed positioning, backdrop blur on scroll, gradient text

### Footer Component  
- **Location**: `components/Footer.js`
- **Pattern**: Dark premium 4-column grid
- **Features**: Brand section, organized navigation, CTA button
- **Classes**: Dark slate background, gradient branding, hover effects

### Homepage
- **Location**: `app/page.js`
- **Pattern**: Premium animated landing page
- **Features**: Animated background, gradient text, feature cards, stats row
- **Classes**: Container grid, feature cards with overlays, animated elements

---

## 🎨 Color System

### Primary Gradient
```css
from-cyan-500 to-blue-600
from-cyan-400 to-blue-500 (hover)
```

### Text Gradient
```css
from-cyan-400 via-blue-500 to-violet-500 (text-gradient utility)
```

### Background
```css
bg-slate-950 (dark background)
bg-slate-900 (secondary)
bg-slate-800/50 (borders)
```

### Accents
```css
text-cyan-400 (primary accent)
text-blue-500 (secondary accent)  
text-slate-300 (light text)
text-slate-400 (lighter text)
```

---

## ✨ Animation Classes

### Gradient Animation (Text)
```css
animate-gradient
/* 3s infinite gradient text color shift */
```

### Pulse Animation (Background)
```css
animate-pulse
/* 4s infinite opacity pulse */
```

### Custom Animations
```css
animate-float      /* 6s floating effect */
animate-fade-in    /* 0.5s entrance opacity */
```

---

## 🔘 Button Styles

### Primary Button
```html
<button className="px-6 py-2 bg-gradient-to-r from-cyan-500 to-blue-600 
                   hover:from-cyan-400 hover:to-blue-500 text-white 
                   font-semibold rounded-lg transition-all duration-300 
                   shadow-lg shadow-cyan-500/20 hover:shadow-cyan-500/40 
                   hover:scale-105">
```

### Secondary Button
```html
<button className="px-4 py-2 bg-slate-700 hover:bg-slate-600 
                   text-white rounded-lg transition-colors">
```

---

## 🎴 Card Styles

### Feature Card with Gradient
```html
<div className="group relative overflow-hidden rounded-lg border border-slate-700 
                bg-gradient-to-br from-slate-800 to-slate-900 
                hover:border-cyan-400/60 transition-all duration-300 
                hover:shadow-xl hover:shadow-cyan-500/20">
  <!-- Content -->
</div>
```

### Glass Card (Glassmorphism)
```html
<div className="backdrop-blur-md bg-white/10 border border-white/20 
                rounded-xl p-6 hover:bg-white/20 transition-all">
  <!-- Content -->
</div>
```

---

## 📝 Typography

### Headings
```css
font-family: Sora
font-weight: 700-800
sizes: h1(2.25rem), h2(1.875rem), h3(1.5rem)
color: white or gradient
```

### Body Text
```css
font-family: Inter
font-weight: 400-500
size: 0.875rem - 1rem
color: slate-300 or slate-400
line-height: 1.5
```

---

## 🎯 Hover Effects

### Button Hover
```css
hover:scale-105           /* 5% scale growth */
hover:shadow-xl           /* Enhanced shadow */
hover:shadow-cyan-500/50  /* Glow effect */
```

### Link Hover
```css
group-hover:translate-x-1 /* Arrow translation */
hover:text-cyan-400       /* Color change */
hover:border-cyan-400     /* Border color */
```

### Card Hover
```css
hover:border-cyan-400/60  /* Border glow */
hover:shadow-xl           /* Shadow growth */
hover:shadow-cyan-500/20  /* Subtle glow */
group-hover:scale-105     /* Group scale */
```

---

## 🔧 Common Utility Classes

### Layout
```css
container mx-auto          /* Centered content */
grid-cols-1 md:grid-cols-3 /* Responsive grid */
flex flex-col md:flex-row  /* Responsive flex */
```

### Spacing
```css
px-4 md:px-6 lg:px-8      /* Responsive padding */
py-6 md:py-8 lg:py-12     /* Responsive vertical */
gap-6 md:gap-8            /* Responsive gaps */
```

### Effects
```css
transition-all duration-300    /* Smooth transitions */
backdrop-blur-md               /* Blur effect */
bg-gradient-to-r              /* Gradient direction */
border-opacity-50             /* Transparent borders */
```

---

## 📱 Responsive Patterns

### Typography Scaling
```css
text-base md:text-lg lg:text-2xl  /* Responsive text */
text-sm md:text-base              /* Medium text */
```

### Display Hiding
```css
hidden sm:inline     /* Hidden mobile, show tablet+ */
block md:hidden       /* Show mobile, hide tablet+ */
flex-col md:flex-row  /* Stack mobile, row tablet+ */
```

### Padding Scaling
```css
p-4 md:p-6 lg:p-8    /* Responsive padding */
py-6 md:py-12        /* Responsive vertical padding */
```

---

## 🎬 Animation Timing

```css
duration-300       /* 300ms smooth transitions */
duration-700       /* 700ms slower animations */
animate-gradient   /* 3s infinite */
animate-pulse      /* 4s infinite */
```

---

## ✅ Component Checklist

Use this when adding new components:

- [ ] Uses Sora font for headings
- [ ] Uses Inter font for body text
- [ ] Has gradient accents (cyan-blue)
- [ ] Includes hover effects (scale/color/shadow)
- [ ] Responsive design (mobile-first)
- [ ] Smooth transitions (300ms minimum)
- [ ] Proper spacing/padding
- [ ] Good color contrast
- [ ] Accessible (semantic HTML, keyboard nav)

---

## 🚀 Quick Copy-Paste

### Premium Button
```html
<button className="px-6 py-2 bg-gradient-to-r from-cyan-500 to-blue-600 
                   hover:from-cyan-400 hover:to-blue-500 text-white 
                   font-semibold rounded-lg transition-all duration-300 
                   shadow-lg shadow-cyan-500/20 hover:shadow-cyan-500/40 
                   hover:scale-105">
  Label
</button>
```

### Premium Card
```html
<div className="group relative rounded-lg border border-slate-700/50 
                bg-slate-900/50 backdrop-blur-sm p-6 
                hover:border-cyan-400/60 hover:shadow-xl 
                hover:shadow-cyan-500/20 transition-all duration-300">
  Content
</div>
```

### Gradient Text
```html
<h1 className="text-4xl font-black bg-gradient-to-r 
               from-cyan-400 to-blue-500 bg-clip-text 
               text-transparent">
  Headline
</h1>
```

---

## 📊 File Reference

| File | Purpose | Status |
|------|---------|--------|
| `components/Header.js` | Navigation + branding | ✅ Premium |
| `components/Footer.js` | Footer with sections | ✅ Premium |
| `app/page.js` | Homepage | ✅ Premium |
| `styles/globals.css` | Typography + utilities | ✅ Professional |
| `tailwind.config.js` | Config + animations | ✅ Enhanced |

---

**Last Updated**: December 29, 2025  
**Status**: Complete and production-ready

