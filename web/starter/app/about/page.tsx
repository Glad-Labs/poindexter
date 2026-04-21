import Link from 'next/link';

export const metadata = {
  title: 'About',
};

/**
 * Placeholder About page. Replace with your own copy, team photos,
 * and whatever else establishes your publication's identity.
 */
export default function AboutPage() {
  return (
    <article className="prose-custom">
      <h1>About</h1>
      <p>
        This site is powered by{' '}
        <a
          href="https://github.com/Glad-Labs/poindexter"
          target="_blank"
          rel="noreferrer"
        >
          Poindexter
        </a>
        , an open-source AI content pipeline that researches, writes, QAs, and
        publishes articles autonomously — with a human approver in the loop.
      </p>
      <p>
        Fork-user: replace this page with your own story. Who are you, why are
        you publishing, what do you write about? Two paragraphs is enough.
      </p>
      <p>
        <Link href="/">← Back to posts</Link>
      </p>
    </article>
  );
}
