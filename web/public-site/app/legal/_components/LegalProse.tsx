import type { ReactNode } from 'react';

/**
 * LegalProse — shared prose shell for /legal/privacy, /terms, /cookie-policy.
 *
 * Not a route, just a colocated component. Caps width, applies the E3
 * prose tokens (cyan links, muted body, mono code, hairline tables,
 * Space-Grotesk headings). Kept out of /legal/layout.tsx so that
 * /legal/data-requests — which is an interactive form, not a text
 * document — doesn't inherit prose styling.
 */
export default function LegalProse({ children }: { children: ReactNode }) {
  return (
    <div className="container mx-auto px-4 sm:px-6 lg:px-8 max-w-3xl">
      <div
        className="prose prose-invert max-w-none
          prose-headings:font-[family-name:var(--gl-font-display)]
          prose-headings:font-bold
          prose-h1:text-4xl md:prose-h1:text-5xl prose-h1:text-white prose-h1:tracking-tight prose-h1:mb-4
          prose-h2:text-2xl prose-h2:text-white prose-h2:mt-10 prose-h2:mb-4 prose-h2:tracking-tight
          prose-h3:text-xl prose-h3:text-white prose-h3:mt-6 prose-h3:mb-3 prose-h3:tracking-tight
          prose-p:text-[color:var(--gl-text-muted)] prose-p:leading-relaxed prose-p:my-4
          prose-strong:text-white prose-strong:font-semibold
          prose-a:text-[color:var(--gl-cyan)] prose-a:hover:opacity-80 prose-a:underline
          prose-li:text-[color:var(--gl-text-muted)] prose-li:marker:text-[color:var(--gl-cyan)]
          prose-ul:my-4 prose-ol:my-4
          prose-code:text-[color:var(--gl-cyan)] prose-code:bg-[color:var(--gl-surface)] prose-code:px-1.5 prose-code:py-0.5 prose-code:rounded-none prose-code:before:content-none prose-code:after:content-none
          prose-hr:border-[color:var(--gl-hairline)]
          prose-table:my-6
          prose-th:text-white prose-th:bg-[color:var(--gl-surface)] prose-th:border prose-th:border-[color:var(--gl-hairline-strong)] prose-th:px-3 prose-th:py-2 prose-th:text-left
          prose-td:border prose-td:border-[color:var(--gl-hairline)] prose-td:px-3 prose-td:py-2 prose-td:text-[color:var(--gl-text-muted)]
          prose-blockquote:border-l-[3px] prose-blockquote:border-[color:var(--gl-cyan)] prose-blockquote:bg-[color:var(--gl-surface)] prose-blockquote:text-[color:var(--gl-text-muted)] prose-blockquote:not-italic prose-blockquote:px-4 prose-blockquote:py-2"
      >
        {children}
      </div>
    </div>
  );
}
