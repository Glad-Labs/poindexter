'use client';

import { useState, useRef, useEffect } from 'react';
import * as Sentry from '@sentry/nextjs';
import Link from 'next/link';
import { searchPosts, getCategories } from '../lib/api-fastapi';

/**
 * SearchBar Component with Category Filter
 * Provides real-time search with dropdown results
 * Features:
 * - Debounced search (300ms)
 * - Category filtering
 * - Dropdown with live results
 * - Keyboard navigation
 * - Click outside to close
 */
export default function SearchBar({ compact = false }) {
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState('all');
  const [categories, setCategories] = useState([]);
  const [results, setResults] = useState([]);
  const [isOpen, setIsOpen] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [selectedIndex, setSelectedIndex] = useState(-1);
  const searchRef = useRef(null);
  const debounceTimer = useRef(null);

  // Load categories on mount (only if expanded view)
  useEffect(() => {
    if (compact) return; // Skip loading in compact header mode

    const loadCategories = async () => {
      try {
        const cats = await getCategories();
        setCategories(cats || []);
      } catch (error) {
        Sentry.captureException(error);
      }
    };
    loadCategories();
  }, [compact]);

  // Debounced search
  useEffect(() => {
    if (debounceTimer.current) {
      clearTimeout(debounceTimer.current);
    }

    if (!query.trim()) {
      setResults([]);
      setIsOpen(false);
      return;
    }

    setIsLoading(true);
    debounceTimer.current = setTimeout(async () => {
      try {
        const posts = await searchPosts(query, 5, {
          category: category !== 'all' ? category : undefined,
        });
        setResults(posts || []);
        setIsOpen(true);
        setSelectedIndex(-1);
      } catch (error) {
        Sentry.captureException(error);
        setResults([]);
      } finally {
        setIsLoading(false);
      }
    }, 300);

    return () => clearTimeout(debounceTimer.current);
  }, [query, category]);

  // Close dropdown when clicking outside
  useEffect(() => {
    function handleClickOutside(event) {
      if (searchRef.current && !searchRef.current.contains(event.target)) {
        setIsOpen(false);
      }
    }

    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  // Keyboard navigation
  const handleKeyDown = (e) => {
    if (!isOpen || results.length === 0) return;

    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault();
        setSelectedIndex((prev) => (prev < results.length - 1 ? prev + 1 : 0));
        break;
      case 'ArrowUp':
        e.preventDefault();
        setSelectedIndex((prev) => (prev > 0 ? prev - 1 : results.length - 1));
        break;
      case 'Enter':
        e.preventDefault();
        if (selectedIndex >= 0 && results[selectedIndex]) {
          handleSelect(results[selectedIndex]);
        }
        break;
      case 'Escape':
        e.preventDefault();
        setIsOpen(false);
        setQuery('');
        break;
      default:
        break;
    }
  };

  const handleSelect = (_post) => {
    setQuery('');
    setResults([]);
    setIsOpen(false);
    // Navigation handled by Link component
  };

  const handleCategoryChange = (e) => {
    setCategory(e.target.value);
    setSelectedIndex(-1);
  };

  return (
    <div ref={searchRef} className="relative w-full">
      {/* Search Input */}
      <div className="relative">
        <input
          type="text"
          placeholder="🔍 Search articles..."
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={handleKeyDown}
          onFocus={() => query && setIsOpen(true)}
          className="w-full px-4 py-2.5 rounded-lg border border-slate-300 bg-white text-slate-900 placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-cyan-500 focus:border-transparent transition-all"
          aria-label="Search articles"
          aria-expanded={isOpen}
          aria-autocomplete="list"
        />
        {isLoading && (
          <div className="absolute right-3 top-1/2 -translate-y-1/2">
            <div className="animate-spin">⟳</div>
          </div>
        )}
      </div>

      {/* Category Filter */}
      {!compact && categories.length > 0 && (
        <div className="mt-2">
          <select
            value={category}
            onChange={handleCategoryChange}
            className="w-full px-3 py-2 rounded-lg border border-slate-300 bg-white text-slate-900 text-sm focus:outline-none focus:ring-2 focus:ring-cyan-500 focus:border-transparent transition-all"
            aria-label="Filter by category"
          >
            <option value="all">All Categories</option>
            {categories.map((cat) => (
              <option key={cat.id || cat.name} value={cat.slug || cat.name}>
                {cat.name}
              </option>
            ))}
          </select>
        </div>
      )}

      {/* Results Dropdown */}
      {isOpen && results.length > 0 && (
        <div
          className="absolute top-full mt-1 w-full bg-white border border-slate-200 rounded-lg shadow-lg z-50 max-h-96 overflow-y-auto"
          role="listbox"
        >
          {results.map((post, index) => (
            <Link
              key={post.id || index}
              href={`/posts/${post.slug}`}
              onClick={() => handleSelect(post)}
            >
              <div
                className={`px-4 py-3 border-b border-slate-100 last:border-b-0 cursor-pointer transition-colors ${
                  index === selectedIndex
                    ? 'bg-cyan-50 border-l-4 border-l-cyan-500'
                    : 'hover:bg-slate-50'
                }`}
                role="option"
                aria-selected={index === selectedIndex}
              >
                <div className="font-medium text-slate-900 line-clamp-1">
                  {post.title}
                </div>
                <div className="text-sm text-slate-600 line-clamp-1">
                  {post.excerpt || post.description}
                </div>
              </div>
            </Link>
          ))}
        </div>
      )}

      {/* No Results State */}
      {isOpen && query && results.length === 0 && !isLoading && (
        <div className="absolute top-full mt-1 w-full bg-white border border-slate-200 rounded-lg shadow-lg z-50 px-4 py-3 text-center text-slate-500">
          No articles found for "{query}"
        </div>
      )}
    </div>
  );
}
