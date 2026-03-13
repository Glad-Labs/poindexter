import Link from 'next/link';

interface PaginationData {
  page: number;
  pageCount: number;
}

interface PaginationProps {
  pagination: PaginationData;
  basePath?: string;
}

export default function Pagination({
  pagination,
  basePath = '/archive',
}: PaginationProps) {
  const { page, pageCount } = pagination;

  if (pageCount <= 1) return null;

  const pages = Array.from({ length: pageCount }, (_, i) => i + 1);
  const prevPage = page > 1 ? page - 1 : null;
  const nextPage = page < pageCount ? page + 1 : null;

  return (
    <nav
      className="flex justify-center items-center py-8"
      aria-label="Pagination navigation"
      role="navigation"
    >
      <ol
        className="flex flex-wrap justify-center items-center gap-2"
        aria-label={`Current page ${page} of ${pageCount}`}
      >
        {/* Previous Page Button */}
        {prevPage && (
          <li role="none">
            <Link
              href={`${basePath}/${prevPage}`}
              className="inline-flex items-center gap-2 px-4 py-2 bg-gray-700 text-white rounded hover:bg-gray-600 focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-500 transition-all"
              aria-label={`Go to previous page (page ${prevPage})`}
            >
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
                  d="M15 19l-7-7 7-7"
                />
              </svg>
              <span className="hidden sm:inline">Previous</span>
              <span className="sr-only">Previous</span>
            </Link>
          </li>
        )}

        {/* Page Numbers */}
        {pages.map((p) => (
          <li key={p} role="none">
            {p === page ? (
              <span
                className="inline-flex items-center justify-center px-4 py-2 rounded bg-cyan-500 text-white font-semibold"
                aria-current="page"
                aria-label={`Current page, page ${p} of ${pageCount}`}
              >
                {p}
              </span>
            ) : (
              <Link
                href={`${basePath}/${p}`}
                className="inline-flex items-center justify-center px-4 py-2 rounded bg-gray-800 text-gray-300 hover:bg-gray-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-500 transition-all"
                aria-label={`Go to page ${p} of ${pageCount}`}
              >
                {p}
              </Link>
            )}
          </li>
        ))}

        {/* Next Page Button */}
        {nextPage && (
          <li role="none">
            <Link
              href={`${basePath}/${nextPage}`}
              className="inline-flex items-center gap-2 px-4 py-2 bg-gray-700 text-white rounded hover:bg-gray-600 focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-500 transition-all"
              aria-label={`Go to next page (page ${nextPage})`}
            >
              <span className="hidden sm:inline">Next</span>
              <span className="sr-only">Next</span>
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
                  d="M9 5l7 7-7 7"
                />
              </svg>
            </Link>
          </li>
        )}
      </ol>

      {/* Page Info for Screen Readers */}
      <div className="sr-only" role="status" aria-live="polite">
        Showing page {page} of {pageCount}
      </div>
    </nav>
  );
}
