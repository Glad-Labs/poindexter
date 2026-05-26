// Tailwind CSS v4 moved its PostCSS plugin out of the ``tailwindcss``
// package and into a separate ``@tailwindcss/postcss`` package.
//
// Pre-2026-05-26 this file imported ``tailwindcss`` directly as the
// PostCSS plugin — that worked through Tailwind v3.x. The
// glad-labs-codebase-public-site production build started failing
// with:
//
//   Error: It looks like you're trying to use ``tailwindcss``
//   directly as a PostCSS plugin. The PostCSS plugin has moved to
//   a separate package, so to continue using Tailwind CSS with
//   PostCSS you'll need to install ``@tailwindcss/postcss`` and
//   update your PostCSS configuration.
//
// after the prod-deps bump in #566 pulled Tailwind to ^4.3.0 alongside
// Next.js 16. The fix is the documented Tailwind v4 migration:
// rename the plugin entry to ``@tailwindcss/postcss`` and add it to
// devDependencies.
module.exports = {
  plugins: {
    '@tailwindcss/postcss': {},
    autoprefixer: {},
  },
};
