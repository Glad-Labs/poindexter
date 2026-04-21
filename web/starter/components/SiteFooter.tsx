/**
 * Site footer. Minimal by design — replace with your own copy, legal
 * links, and social icons.
 */
export function SiteFooter() {
  return (
    <footer className="mt-16 border-t border-gray-200 py-8 text-sm text-brand-muted">
      <div className="mx-auto max-w-3xl px-4">
        <p>
          Built with{' '}
          <a
            href="https://github.com/Glad-Labs/poindexter"
            className="text-brand-accent hover:underline"
          >
            Poindexter
          </a>{' '}
          — the open-source AI content pipeline.
        </p>
      </div>
    </footer>
  );
}
