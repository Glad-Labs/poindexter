import Link from 'next/link';

export default function NotFound() {
  return (
    <div className="rounded-md border border-gray-200 bg-white p-8 text-center">
      <h1 className="text-2xl font-semibold">Not found.</h1>
      <p className="mt-2 text-sm text-brand-muted">
        That URL doesn&apos;t match any published post.
      </p>
      <Link
        href="/"
        className="mt-4 inline-block text-sm text-brand-accent hover:underline"
      >
        Back to all posts
      </Link>
    </div>
  );
}
