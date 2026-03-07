import logger from './logger';
import matter from 'gray-matter';

/**
 * Calculate reading time in minutes for text content
 * Average reading speed: 200 words per minute
 */
export function calculateReadingTime(content) {
  if (!content || typeof content !== 'string') {
    return 0;
  }

  // Count words (rough estimation)
  const wordCount = content.trim().split(/\s+/).length;
  const readingTime = Math.ceil(wordCount / 200);

  return Math.max(1, readingTime); // At least 1 minute
}

/**
 * Format date to readable string
 * Returns: "October 25, 2025"
 */
export function formatDate(dateString) {
  if (!dateString) {
    return '';
  }

  try {
    const date = new Date(dateString);
    if (isNaN(date.getTime())) {
      return '';
    }

    return date.toLocaleDateString('en-US', {
      year: 'numeric',
      month: 'long',
      day: 'numeric',
    });
  } catch (error) {
    if (process.env.NODE_ENV !== 'production') {
      logger.error('Date formatting error:', error);
    }
    return '';
  }
}

/**
 * Format date to ISO format for schema.org
 * Returns: "2025-10-25"
 */
export function formatDateISO(dateString) {
  if (!dateString) {
    return '';
  }

  try {
    const date = new Date(dateString);
    if (isNaN(date.getTime())) {
      return '';
    }

    return date.toISOString().split('T')[0];
  } catch (error) {
    if (process.env.NODE_ENV !== 'production') {
      logger.error('ISO date formatting error:', error);
    }
    return '';
  }
}

/**
 * Format date relative to now
 * Returns: "2 days ago", "1 month ago", etc.
 */
export function formatDateRelative(dateString) {
  if (!dateString) {
    return '';
  }

  try {
    const date = new Date(dateString);
    if (isNaN(date.getTime())) {
      return '';
    }

    const now = new Date();
    const seconds = Math.floor((now - date) / 1000);

    // Less than a minute
    if (seconds < 60) {
      return 'just now';
    }

    // Minutes
    if (seconds < 3600) {
      const minutes = Math.floor(seconds / 60);
      return `${minutes} minute${minutes > 1 ? 's' : ''} ago`;
    }

    // Hours
    if (seconds < 86400) {
      const hours = Math.floor(seconds / 3600);
      return `${hours} hour${hours > 1 ? 's' : ''} ago`;
    }

    // Days
    if (seconds < 604800) {
      const days = Math.floor(seconds / 86400);
      return `${days} day${days > 1 ? 's' : ''} ago`;
    }

    // Weeks
    if (seconds < 2592000) {
      const weeks = Math.floor(seconds / 604800);
      return `${weeks} week${weeks > 1 ? 's' : ''} ago`;
    }

    // Months
    if (seconds < 31536000) {
      const months = Math.floor(seconds / 2592000);
      return `${months} month${months > 1 ? 's' : ''} ago`;
    }

    // Years
    const years = Math.floor(seconds / 31536000);
    return `${years} year${years > 1 ? 's' : ''} ago`;
  } catch (error) {
    if (process.env.NODE_ENV !== 'production') {
      logger.error('Relative date formatting error:', error);
    }
    return '';
  }
}

/**
 * Generate excerpt from content
 * Truncates at word boundary with ellipsis
 */
export function generateExcerpt(content, maxLength = 160) {
  if (!content || typeof content !== 'string') {
    return '';
  }

  // Remove markdown syntax
  const cleanContent = content
    .replace(/[#*`_[\]()]/g, '')
    .replace(/\n\n+/g, ' ')
    .replace(/\n/g, ' ')
    .trim();

  // Truncate at word boundary
  if (cleanContent.length > maxLength) {
    const truncated = cleanContent.substring(0, maxLength);
    return truncated.substring(0, truncated.lastIndexOf(' ')) + '...';
  }

  return cleanContent;
}

/**
 * Estimate reading time with more detailed breakdown
 */
export function getReadingTimeDetails(content) {
  const readingTime = calculateReadingTime(content);

  let level = 'Quick read';
  if (readingTime > 3 && readingTime <= 7) {
    level = 'Medium read';
  } else if (readingTime > 7) {
    level = 'Long read';
  }

  return {
    minutes: readingTime,
    level,
    displayText: `${readingTime} min read • ${level}`,
  };
}

/**
 * Extract first image URL from markdown content
 */
export function extractFirstImage(content) {
  if (!content || typeof content !== 'string') {
    return null;
  }

  // Match markdown image syntax: ![alt](url)
  const markdownImageRegex = /!\[.*?\]\((.*?)\)/;
  const match = content.match(markdownImageRegex);

  if (match && match[1]) {
    return match[1];
  }

  return null;
}

/**
 * Extract all headings from markdown content
 * Returns array of { level, text, id }
 */
export function extractHeadings(content) {
  if (!content || typeof content !== 'string') {
    return [];
  }

  const headingRegex = /^(#{1,6})\s+(.+?)$/gm;
  const headings = [];
  let match;

  while ((match = headingRegex.exec(content)) !== null) {
    const level = match[1].length; // # = 1, ## = 2, etc.
    const text = match[2].trim();
    const id = text
      .toLowerCase()
      .replace(/\s+/g, '-')
      .replace(/[^\w-]/g, '');

    headings.push({ level, text, id });
  }

  return headings;
}

/**
 * Generate table of contents from headings
 */
export function generateTableOfContents(content) {
  const headings = extractHeadings(content);

  if (headings.length === 0) {
    return null;
  }

  return headings
    .filter((h) => h.level <= 3) // Only H1, H2, H3
    .map((h) => ({
      ...h,
      indent: h.level - 1,
    }));
}

/**
 * Parse frontmatter from markdown content
 * Returns: { frontmatter: { ...}, content: '...' }
 */
export function parseFrontmatter(markdownContent) {
  try {
    const { data, content } = matter(markdownContent);
    return { frontmatter: data, content };
  } catch (error) {
    if (process.env.NODE_ENV !== 'production') {
      logger.error('Frontmatter parsing error:', error);
    }
    return { frontmatter: {}, content: markdownContent };
  }
}

/**
 * Get word count for content
 */
export function getWordCount(content) {
  if (!content || typeof content !== 'string') {
    return 0;
  }

  return content.trim().split(/\s+/).length;
}

/**
 * Highlight search query in text
 * Returns text with query wrapped in <mark> tags
 */
export function highlightQuery(text, query) {
  if (!text || !query) {
    return text;
  }

  const regex = new RegExp(`(${query})`, 'gi');
  return text.replace(regex, '<mark>$1</mark>');
}

/**
 * Generate slug from title
 */
export function generateSlug(title) {
  if (!title || typeof title !== 'string') {
    return '';
  }

  return title
    .toLowerCase()
    .trim()
    .replace(/[^\w\s-]/g, '') // Remove special characters
    .replace(/\s+/g, '-') // Replace spaces with hyphens
    .replace(/-+/g, '-'); // Replace multiple hyphens with single hyphen
}

/**
 * Truncate text to specified length
 */
export function truncateText(text, maxLength = 100) {
  if (!text || text.length <= maxLength) {
    return text;
  }

  return text.substring(0, maxLength).trim() + '...';
}

/**
 * Get initials from author name for avatar
 */
export function getInitials(name) {
  if (!name || typeof name !== 'string') {
    return 'A';
  }

  return name
    .split(' ')
    .map((word) => word[0])
    .join('')
    .toUpperCase()
    .substring(0, 2);
}

/**
 * Strip HTML tags from content
 * Used for word counting and plain text extraction
 */
export function stripHtmlTags(html) {
  if (!html || typeof html !== 'string') {
    return '';
  }

  return html
    .replace(/<[^>]*>/g, '') // Remove HTML tags
    .replace(/&nbsp;/g, ' ') // Replace non-breaking spaces
    .replace(/&amp;/g, '&') // Replace HTML entities
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .trim();
}
