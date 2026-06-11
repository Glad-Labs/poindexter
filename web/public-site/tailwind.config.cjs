/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './app/**/*.{js,ts,jsx,tsx,mdx}',
    './components/**/*.{js,ts,jsx,tsx,mdx}',
    './lib/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    fontFamily: {
      sans: ['Inter', 'system-ui', 'sans-serif'],
      sora: ['Sora', 'system-ui', 'sans-serif'],
      mono: ['Fira Code', 'Courier New', 'monospace'],
    },
    extend: {
      colors: {
        slate: {
          950: '#030712',
          925: '#0a0f1f',
        },
      },
      backdropBlur: {
        xs: '2px',
        xl: '16px',
      },
      boxShadow: {
        'glow-cyan': '0 0 20px rgba(34, 211, 238, 0.3)',
        'glow-blue': '0 0 20px rgba(59, 130, 246, 0.3)',
        'glow-xl': '0 20px 40px rgba(0, 0, 0, 0.4)',
      },
    },
  },
  plugins: [require('@tailwindcss/typography')],
};
