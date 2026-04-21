/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ['./app/**/*.{ts,tsx}', './components/**/*.{ts,tsx}'],
  theme: {
    extend: {
      // Fork-users: your brand lives here. Replace the neutral grays with
      // your palette, add brand fonts, custom radii, etc.
      colors: {
        brand: {
          DEFAULT: '#111827', // near-black
          muted: '#6b7280', // slate-500
          surface: '#f9fafb', // near-white background
          accent: '#2563eb', // blue-600 — generic CTA
        },
      },
      fontFamily: {
        sans: [
          'ui-sans-serif',
          'system-ui',
          '-apple-system',
          'Segoe UI',
          'sans-serif',
        ],
      },
    },
  },
  plugins: [],
};
