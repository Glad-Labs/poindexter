import logger from './logger';
/**
 * Google Analytics 4 Event Tracking Utilities
 *
 * Comprehensive analytics tracking for content engagement, user behavior,
 * and performance metrics. Includes reading depth tracking, timing metrics,
 * and custom event categorization.
 *
 * Integration: Google Analytics 4 property ID via environment variable
 * NEXT_PUBLIC_GA4_ID=G-XXXXXXXXXX
 */

/**
 * Check if Google Analytics is available
 */
const isGAReady = () => {
  if (typeof window === 'undefined') return false;
  return typeof window.gtag === 'function';
};

/**
 * Track page view event
 * Automatically called when page loads
 *
 * @param {string} path - Page path (e.g., '/posts/my-article')
 * @param {string} title - Page title
 * @param {string} type - Page type: 'home', 'post', 'archive', 'category', 'tag', 'search', 'error'
 * @param {Object} metadata - Optional metadata object
 */
export const trackPageView = (path, title, type = 'page', metadata = {}) => {
  if (!isGAReady()) return;

  try {
    window.gtag('event', 'page_view', {
      page_path: path,
      page_title: title,
      page_type: type,
      ...metadata,
      timestamp: new Date().toISOString(),
    });
  } catch (error) {
    logger.error('Analytics: trackPageView error', error);
  }
};

/**
 * Track custom event
 *
 * @param {string} eventName - Event name (e.g., 'search', 'post_click', 'share')
 * @param {Object} eventParams - Event parameters
 * @param {string} eventParams.category - Event category (e.g., 'engagement', 'search', 'navigation')
 * @param {string} eventParams.label - Event label (e.g., 'search_query', 'related_post')
 * @param {number} eventParams.value - Numeric value (optional)
 */
export const trackEvent = (eventName, eventParams = {}) => {
  if (!isGAReady()) return;

  try {
    const { category = 'general', label = '', value, ...rest } = eventParams;

    window.gtag('event', eventName, {
      event_category: category,
      event_label: label,
      ...(value !== undefined && { value }),
      ...rest,
      timestamp: new Date().toISOString(),
    });
  } catch (error) {
    logger.error('Analytics: trackEvent error', error);
  }
};

/**
 * Track page performance timing
 * Useful for Core Web Vitals and custom timing metrics
 *
 * @param {string} metricName - Metric name (e.g., 'page_load', 'content_render', 'api_call')
 * @param {number} duration - Duration in milliseconds
 * @param {Object} metadata - Additional metadata
 */
export const trackTiming = (metricName, duration, metadata = {}) => {
  if (!isGAReady()) return;

  try {
    window.gtag('event', 'timing_complete', {
      name: metricName,
      value: Math.round(duration),
      ...metadata,
      timestamp: new Date().toISOString(),
    });
  } catch (error) {
    logger.error('Analytics: trackTiming error', error);
  }
};

/**
 * Track exception/error event
 *
 * @param {string} description - Error description
 * @param {boolean} fatal - Whether error is fatal (stops functionality)
 * @param {Object} metadata - Error context metadata
 */
export const trackException = (description, fatal = false, metadata = {}) => {
  if (!isGAReady()) return;

  try {
    window.gtag('event', 'exception', {
      description,
      fatal,
      ...metadata,
      timestamp: new Date().toISOString(),
    });
  } catch (error) {
    logger.error('Analytics: trackException error', error);
  }
};

/**
 * Track article/content engagement metrics
 *
 * @param {string} postId - Post ID
 * @param {string} postTitle - Post title
 * @param {string} category - Post category
 * @param {number} readingTime - Estimated reading time in minutes
 */
export const trackArticleView = (
  postId,
  postTitle,
  category = '',
  readingTime = 0
) => {
  if (!isGAReady()) return;

  try {
    trackEvent('view_article', {
      category: 'content',
      label: postTitle,
      post_id: postId,
      post_category: category,
      reading_time_minutes: readingTime,
    });
  } catch (error) {
    logger.error('Analytics: trackArticleView error', error);
  }
};

/**
 * Track search event
 *
 * @param {string} searchQuery - The search query entered
 * @param {number} resultsCount - Number of results returned
 * @param {string} source - Search source ('header', 'sidebar', 'dedicated_page')
 */
export const trackSearch = (
  searchQuery,
  resultsCount = 0,
  source = 'header'
) => {
  if (!isGAReady()) return;

  try {
    trackEvent('search', {
      category: 'search',
      label: searchQuery,
      search_query: searchQuery,
      results_count: resultsCount,
      search_source: source,
    });
  } catch (error) {
    logger.error('Analytics: trackSearch error', error);
  }
};

/**
 * Track reading depth
 * Monitors how far down the page user scrolls
 * Depth levels: 25%, 50%, 75%, 100% (viewed full article)
 *
 * @param {number} percentRead - Percentage of content read (0-100)
 * @param {string} postId - Post ID (for attribution)
 */
export const trackReadingDepth = (percentRead, postId = '') => {
  if (!isGAReady()) return;

  try {
    let depthLevel = 'unknown';

    if (percentRead >= 100) {
      depthLevel = 'full_read';
    } else if (percentRead >= 75) {
      depthLevel = 'read_75';
    } else if (percentRead >= 50) {
      depthLevel = 'read_50';
    } else if (percentRead >= 25) {
      depthLevel = 'read_25';
    }

    trackEvent('reading_depth', {
      category: 'engagement',
      label: depthLevel,
      percent_read: Math.round(percentRead),
      depth_level: depthLevel,
      post_id: postId,
    });
  } catch (error) {
    logger.error('Analytics: trackReadingDepth error', error);
  }
};

/**
 * Track time on page
 * Call this when user leaves or navigates away
 *
 * @param {number} timeSpentSeconds - Seconds spent on page
 * @param {string} pageType - Type of page ('post', 'home', 'archive')
 */
export const trackTimeOnPage = (timeSpentSeconds, pageType = 'page') => {
  if (!isGAReady()) return;

  try {
    trackEvent('time_on_page', {
      category: 'engagement',
      label: pageType,
      time_spent_seconds: Math.round(timeSpentSeconds),
      page_type: pageType,
    });
  } catch (error) {
    logger.error('Analytics: trackTimeOnPage error', error);
  }
};

/**
 * Track related post click
 * Measures effectiveness of recommendations
 *
 * @param {string} relatedPostId - ID of related post clicked
 * @param {string} relatedPostTitle - Title of related post
 * @param {string} sourcePostId - ID of post where related was shown
 */
export const trackRelatedPostClick = (
  relatedPostId,
  relatedPostTitle,
  sourcePostId = ''
) => {
  if (!isGAReady()) return;

  try {
    trackEvent('click_related_post', {
      category: 'engagement',
      label: relatedPostTitle,
      related_post_id: relatedPostId,
      source_post_id: sourcePostId,
    });
  } catch (error) {
    logger.error('Analytics: trackRelatedPostClick error', error);
  }
};

/**
 * Track category or tag click/view
 *
 * @param {string} filterType - 'category' or 'tag'
 * @param {string} filterValue - Category or tag name
 * @param {number} resultsCount - Number of posts in this filter
 */
export const trackFilterClick = (filterType, filterValue, resultsCount = 0) => {
  if (!isGAReady()) return;

  try {
    trackEvent(`click_${filterType}`, {
      category: 'navigation',
      label: filterValue,
      filter_type: filterType,
      filter_value: filterValue,
      results_count: resultsCount,
    });
  } catch (error) {
    logger.error('Analytics: trackFilterClick error', error);
  }
};

/**
 * Track navigation event (internal link clicks)
 *
 * @param {string} destination - Destination path
 * @param {string} linkText - Link text/label
 * @param {string} navType - Type of navigation ('header', 'footer', 'sidebar', 'inline')
 */
export const trackNavigation = (
  destination,
  linkText = '',
  navType = 'navigation'
) => {
  if (!isGAReady()) return;

  try {
    trackEvent('navigation', {
      category: 'navigation',
      label: linkText || destination,
      destination,
      nav_type: navType,
    });
  } catch (error) {
    logger.error('Analytics: trackNavigation error', error);
  }
};

/**
 * Track 404/404 error page view
 *
 * @param {string} requestedPath - Path user tried to access
 * @param {string} referrer - Referrer URL (where they came from)
 */
export const track404 = (requestedPath, referrer = '') => {
  if (!isGAReady()) return;

  try {
    trackEvent('page_not_found', {
      category: 'error',
      label: requestedPath,
      requested_path: requestedPath,
      referrer: referrer || document.referrer,
    });
  } catch (error) {
    logger.error('Analytics: track404 error', error);
  }
};

/**
 * Initialize reading depth tracking for an article
 * Automatically tracks scroll depth at 25%, 50%, 75%, 100%
 * Returns cleanup function to call on unmount
 *
 * @param {string} postId - Post ID for attribution
 * @param {Object} options - Configuration options
 * @returns {Function} Cleanup function (call on unmount)
 *
 * Usage:
 * useEffect(() => {
 *   const cleanup = setupReadingDepthTracking('post-123', { maxScrollWait: 500 });
 *   return cleanup;
 * }, []);
 */
export const setupReadingDepthTracking = (postId = '', options = {}) => {
  if (typeof window === 'undefined') return () => {};

  const { maxScrollWait = 500, thresholds = [0.25, 0.5, 0.75, 1] } = options;

  let trackedDepths = new Set();
  let scrollTimeout = null;

  const calculateReadingDepth = () => {
    const windowHeight = window.innerHeight;
    const documentHeight = document.documentElement.scrollHeight;
    const scrollTop = window.scrollY;

    // Calculate percentage of page scrolled
    const maxScroll = documentHeight - windowHeight;
    const percentRead = maxScroll > 0 ? (scrollTop / maxScroll) * 100 : 0;

    // Track thresholds (25%, 50%, 75%, 100%)
    thresholds.forEach((threshold) => {
      const targetPercent = threshold * 100;
      if (percentRead >= targetPercent && !trackedDepths.has(threshold)) {
        trackedDepths.add(threshold);
        trackReadingDepth(targetPercent, postId);
      }
    });
  };

  const handleScroll = () => {
    clearTimeout(scrollTimeout);
    scrollTimeout = setTimeout(calculateReadingDepth, maxScrollWait);
  };

  window.addEventListener('scroll', handleScroll, { passive: true });

  // Cleanup function
  return () => {
    window.removeEventListener('scroll', handleScroll);
    clearTimeout(scrollTimeout);
  };
};

/**
 * Initialize time on page tracking
 * Measures time between page load and unload/navigation
 * Returns cleanup function to call on unmount
 *
 * @param {string} pageType - Type of page ('post', 'home', 'archive')
 * @returns {Function} Cleanup function (call on unmount)
 *
 * Usage:
 * useEffect(() => {
 *   const cleanup = setupTimeOnPageTracking('post');
 *   return cleanup;
 * }, []);
 */
export const setupTimeOnPageTracking = (pageType = 'page') => {
  if (typeof window === 'undefined') return () => {};

  const startTime = Date.now();

  const handleBeforeUnload = () => {
    const timeSpentMs = Date.now() - startTime;
    const timeSpentSeconds = Math.round(timeSpentMs / 1000);

    // Use sendBeacon to ensure it sends before page unloads
    if (navigator.sendBeacon && isGAReady()) {
      try {
        trackTimeOnPage(timeSpentSeconds, pageType);
      } catch (error) {
        logger.error('Analytics: setupTimeOnPageTracking error', error);
      }
    }
  };

  // Track on page visibility change (tab switching)
  const handleVisibilityChange = () => {
    if (document.hidden) {
      const timeSpentMs = Date.now() - startTime;
      const timeSpentSeconds = Math.round(timeSpentMs / 1000);
      if (timeSpentSeconds > 1) {
        trackTimeOnPage(timeSpentSeconds, pageType);
      }
    }
  };

  window.addEventListener('beforeunload', handleBeforeUnload);
  document.addEventListener('visibilitychange', handleVisibilityChange);

  // Cleanup function
  return () => {
    window.removeEventListener('beforeunload', handleBeforeUnload);
    document.removeEventListener('visibilitychange', handleVisibilityChange);
  };
};

/**
 * Get current GA4 tracking ID
 * Useful for debugging or conditional tracking
 *
 * @returns {string|null} GA4 tracking ID or null
 */
export const getGA4TrackingId = () => {
  return process.env.NEXT_PUBLIC_GA4_ID || null;
};

/**
 * Check if GA4 script is loaded
 * Useful for conditional rendering or tracking setup
 *
 * @returns {boolean}
 */
export const isGA4Loaded = () => {
  return typeof window !== 'undefined' && typeof window.gtag === 'function';
};

/**
 * Utility: Format bytes for analytics (e.g., "2.5 MB")
 * Used for media tracking
 *
 * @param {number} bytes
 * @returns {string}
 */
export const formatBytes = (bytes) => {
  if (bytes === 0) return '0 Bytes';
  const k = 1024;
  const sizes = ['Bytes', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return Math.round((bytes / Math.pow(k, i)) * 100) / 100 + ' ' + sizes[i];
};

/**
 * Utility: Calculate reading speed (words per minute)
 * Used for reading time estimation
 *
 * @param {string} text - Text content
 * @param {number} readingTimeMinutes - Reading time in minutes
 * @returns {number} Estimated words per minute
 */
export const calculateReadingSpeed = (text, readingTimeMinutes) => {
  if (readingTimeMinutes === 0) return 0;
  const wordCount = text.trim().split(/\s+/).length;
  return Math.round(wordCount / readingTimeMinutes);
};

export default {
  // Core tracking functions
  trackPageView,
  trackEvent,
  trackTiming,
  trackException,

  // Specialized tracking
  trackArticleView,
  trackSearch,
  trackReadingDepth,
  trackTimeOnPage,
  trackRelatedPostClick,
  trackFilterClick,
  trackNavigation,
  track404,

  // Setup functions (return cleanup)
  setupReadingDepthTracking,
  setupTimeOnPageTracking,

  // Utilities
  isGAReady,
  getGA4TrackingId,
  isGA4Loaded,
  formatBytes,
  calculateReadingSpeed,
};
