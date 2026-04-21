import { listPosts } from '@/lib/api';
import { PostCard } from '@/components/PostCard';

export const revalidate = 300; // 5 min

/**
 * Home page — paginated list of published posts.
 *
 * Fork-users: add hero copy, featured post, categories, newsletter
 * signup, etc. The only required piece is the `listPosts()` fetch.
 */
export default async function HomePage() {
  let posts: Awaited<ReturnType<typeof listPosts>>['items'] = [];
  let total = 0;
  let error: string | null = null;

  try {
    const page = await listPosts(12, 0);
    posts = page.items;
    total = page.total;
  } catch (err) {
    error =
      err instanceof Error
        ? err.message
        : 'Unable to reach the Poindexter backend.';
  }

  if (error) {
    return (
      <div className="rounded-md border border-red-200 bg-red-50 p-5 text-sm text-red-900">
        <p className="font-semibold">Cannot load posts.</p>
        <p className="mt-1">{error}</p>
        <p className="mt-3 text-red-800">
          Is the backend running at{' '}
          <code className="font-mono">
            {process.env.NEXT_PUBLIC_POINDEXTER_API_URL ||
              'http://localhost:8002'}
          </code>
          ? Override with{' '}
          <code className="font-mono">NEXT_PUBLIC_POINDEXTER_API_URL</code>.
        </p>
      </div>
    );
  }

  if (posts.length === 0) {
    return (
      <div className="rounded-md border border-gray-200 bg-white p-8 text-center">
        <p className="text-lg font-semibold">No posts yet.</p>
        <p className="mt-2 text-sm text-brand-muted">
          Submit your first topic via the CLI or the MCP server and the pipeline
          will queue, write, QA, and land it here.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-3xl font-semibold tracking-tight">Latest posts</h1>
        <p className="mt-1 text-sm text-brand-muted">{total} published</p>
      </header>
      <div className="grid gap-5 sm:grid-cols-2">
        {posts.map((p) => (
          <PostCard key={p.id} post={p} />
        ))}
      </div>
    </div>
  );
}
