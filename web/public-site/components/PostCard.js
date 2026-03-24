import Link from 'next/link';
import Image from 'next/image';

const safeFormatDate = (value) => {
  if (!value) return '';
  const d = new Date(value);
  return isNaN(d.getTime())
    ? ''
    : d.toLocaleDateString('en-US', {
        year: 'numeric',
        month: 'long',
        day: 'numeric',
      });
};

/**
 * Simple markdown text renderer for excerpts
 * Handles: **bold**, *italic*, ***bold italic***
 * Returns JSX with styled formatting
 */
const MarkdownText = ({ text }) => {
  if (!text) return null;

  // Split text by markdown patterns while preserving the markdown markers
  const parts = text.split(
    /(\*\*\*[\s\S]*?\*\*\*|\*\*[\s\S]*?\*\*|\*[\s\S]*?\*)/
  );

  return parts.map((part, index) => {
    if (!part) return null;

    // Bold italic: ***text***
    if (part.startsWith('***') && part.endsWith('***')) {
      return (
        <strong key={index} className="italic font-semibold text-cyan-300">
          {part.slice(3, -3)}
        </strong>
      );
    }

    // Bold: **text**
    if (part.startsWith('**') && part.endsWith('**')) {
      return (
        <strong key={index} className="font-semibold text-slate-100">
          {part.slice(2, -2)}
        </strong>
      );
    }

    // Italic: *text*
    if (part.startsWith('*') && part.endsWith('*')) {
      return (
        <em key={index} className="italic text-slate-200">
          {part.slice(1, -1)}
        </em>
      );
    }

    // Regular text
    return <span key={index}>{part}</span>;
  });
};

/**
 * PostCard renders a single blog post preview card.
 *
 * Heading context: PostCard is designed to be used inside a section that has
 * its own <h2> heading. The post title therefore defaults to an <h3> to
 * maintain correct document outline (h1 > h2 [section] > h3 [card title]).
 *
 * If you embed PostCard in a context where the parent already uses <h3>
 * (e.g., inside an <h2>-less widget), pass `headingLevel={4}` to avoid
 * skipping heading levels (WCAG 1.3.1).
 *
 * @param {object} props.post - Post data object
 * @param {2|3|4|5|6} [props.headingLevel=3] - HTML heading level for the post title
 */
const PostCard = ({ post, headingLevel = 3 }) => {
  const { title, excerpt, slug, published_at, cover_image_url } = post;
  const HeadingTag = `h${headingLevel}`;

  const href = slug ? `/posts/${slug}` : '#';
  const isClickable = Boolean(slug);
  const displayDate = safeFormatDate(published_at);
  const parsedDate = published_at ? new Date(published_at) : null;
  const dateISO =
    parsedDate && !isNaN(parsedDate.getTime())
      ? parsedDate.toISOString().split('T')[0]
      : new Date().toISOString().split('T')[0];

  return (
    <article
      className="group relative card-glass hover:card-gradient transition-all duration-300 overflow-hidden h-full flex flex-col focus-within:ring-2 focus-within:ring-cyan-400"
      aria-labelledby={`post-title-${slug}`}
    >
      {/* Cover Image with overlay effect */}
      {cover_image_url && (
        <div className="relative h-56 w-full overflow-hidden bg-gradient-to-br from-slate-800 to-slate-900">
          <Image
            src={cover_image_url}
            alt={`Cover image for ${title}`}
            fill
            className="object-cover transition-transform duration-500 group-hover:scale-110"
            sizes="(max-width: 768px) 100vw, (max-width: 1200px) 50vw, 33vw"
          />
          {/* Gradient overlay */}
          <div className="absolute inset-0 bg-gradient-to-t from-slate-950 via-transparent to-transparent opacity-40" />
        </div>
      )}

      {/* Card Content */}
      <div className="p-6 flex flex-col h-full relative z-10">
        {/* Published Date - Semantic time element */}
        <div className="mb-4 flex items-center gap-2 text-xs text-slate-400 group-hover:text-cyan-300 transition-colors">
          <svg
            className="w-4 h-4"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
            aria-hidden="true"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"
            />
          </svg>
          <time dateTime={dateISO} className="font-medium">
            {displayDate}
          </time>
        </div>

        {/* Post Title — heading level controlled by headingLevel prop (default h3).
            Callers must pass headingLevel matching the document outline so that
            screen reader heading navigation is unambiguous (WCAG 1.3.1). */}
        <HeadingTag
          id={`post-title-${slug}`}
          className="text-xl md:text-2xl font-bold mb-3 text-slate-100 leading-tight line-clamp-2 group-hover:text-transparent group-hover:bg-gradient-to-r group-hover:from-cyan-400 group-hover:to-blue-500 group-hover:bg-clip-text transition-all duration-300"
        >
          <Link
            href={href}
            className="focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400 focus-visible:rounded-lg px-1 py-0.5"
            tabIndex={isClickable ? 0 : -1}
            aria-disabled={!isClickable}
          >
            {title}
          </Link>
        </HeadingTag>

        {/* Excerpt - Premium typography with markdown formatting */}
        <p className="text-slate-300 mb-6 flex-grow line-clamp-3 text-sm leading-relaxed">
          <MarkdownText text={excerpt} />
        </p>

        {/* Read More Link - Premium style */}
        {/* aria-hidden: the title link above already links to the same URL; this
            is a visual affordance only so we hide it from screen readers to
            avoid announcing two links with the same destination per card. */}
        <div className="inline-flex" aria-hidden="true">
          <Link
            href={href}
            className="inline-flex items-center gap-2 text-sm font-semibold text-cyan-400 hover:text-cyan-300 group/link focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400 focus-visible:rounded-lg px-2 py-1 transition-all duration-300"
            tabIndex={-1}
            aria-hidden="true"
          >
            Read Article
            <svg
              className="w-4 h-4 group-hover/link:translate-x-1 transition-transform duration-300"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
              aria-hidden="true"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M13 7l5 5m0 0l-5 5m5-5H6"
              />
            </svg>
          </Link>
        </div>
      </div>

      {/* Subtle gradient border on hover */}
      <div className="absolute inset-0 rounded-2xl bg-gradient-to-r from-cyan-500/0 via-transparent to-blue-500/0 group-hover:from-cyan-500/10 group-hover:to-blue-500/10 pointer-events-none transition-all duration-300" />
    </article>
  );
};

export default PostCard;
