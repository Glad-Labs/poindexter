// generateMetadata was removed from this layout (#1328 item 6) — it was always
// overridden by the page's own generateMetadata and had no effect.
export default function ArchiveLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return children;
}
