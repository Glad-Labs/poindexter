'use client';

import { useState } from 'react';

interface Heading {
  level: number;
  text: string;
  id: string;
  indent: number;
}

interface TableOfContentsProps {
  headings: Heading[];
}

export function TableOfContents({ headings }: TableOfContentsProps) {
  const [isOpen, setIsOpen] = useState(true);

  if (!headings || headings.length === 0) {
    return null;
  }

  const getIndentClass = (indent: number) => {
    if (indent === 0) return 'font-medium pl-0';
    if (indent === 1) return 'pl-4 text-slate-400';
    return 'pl-8 text-slate-500';
  };

  return (
    <div className="rounded-lg border border-slate-700/50 bg-slate-800/30 p-6 mb-8">
      {/* Heading outside button — <h3> inside <button> is invalid HTML (phrasing content model) */}
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-lg font-semibold text-cyan-400">
          Table of Contents
        </h3>
        <button
          onClick={() => setIsOpen(!isOpen)}
          aria-expanded={isOpen}
          aria-controls="toc-list"
          className="hover:text-cyan-400 transition-colors"
        >
          <span className="sr-only">
            {isOpen ? 'Collapse' : 'Expand'} table of contents
          </span>
          <svg
            className={`w-5 h-5 transition-transform ${isOpen ? 'rotate-180' : ''}`}
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
            aria-hidden="true"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M19 14l-7 7m0 0l-7-7m7 7V3"
            />
          </svg>
        </button>
      </div>

      {isOpen && (
        <nav id="toc-list" aria-label="Table of contents" className="space-y-2">
          {headings.map((heading) => (
            <a
              key={heading.id}
              href={`#${heading.id}`}
              className={`block text-sm transition-colors hover:text-cyan-400 text-slate-300 ${getIndentClass(heading.indent)}`}
              onClick={(e) => {
                e.preventDefault();
                const element = document.getElementById(heading.id);
                if (element) {
                  element.scrollIntoView({ behavior: 'smooth' });
                }
              }}
            >
              {heading.text}
            </a>
          ))}
        </nav>
      )}
    </div>
  );
}
