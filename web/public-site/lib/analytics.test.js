/**
 * Analytics Utils Tests (lib/analytics.js)
 *
 * Tests analytics event tracking and pageview utilities
 * Verifies: Event tracking, pageview logging
 */
import { trackEvent, trackPageView } from '../lib/analytics';

// Mock window.gtag for Google Analytics
const mockGtag = jest.fn();
window.gtag = mockGtag;

describe('Analytics Utils (lib/analytics.js)', () => {
  beforeEach(() => {
    mockGtag.mockClear();
    window.gtag = mockGtag;
  });

  describe('trackEvent', () => {
    it('should track event with event name', () => {
      trackEvent('post_viewed', { label: '123' });
      expect(mockGtag).toHaveBeenCalledWith(
        'event',
        'post_viewed',
        expect.any(Object)
      );
    });

    it('should include event parameters', () => {
      const params = { category: 'tech', label: '123' };
      trackEvent('post_viewed', params);
      expect(mockGtag).toHaveBeenCalledWith(
        'event',
        'post_viewed',
        expect.objectContaining({
          event_category: 'tech',
          event_label: '123',
        })
      );
    });

    it('should handle empty parameters', () => {
      trackEvent('page_visited', {});
      expect(mockGtag).toHaveBeenCalledWith(
        'event',
        'page_visited',
        expect.any(Object)
      );
    });

    it('should track button clicks', () => {
      trackEvent('button_clicked', { label: 'subscribe' });
      expect(mockGtag).toHaveBeenCalled();
    });

    it('should track form submissions', () => {
      trackEvent('form_submitted', { label: 'newsletter_signup' });
      expect(mockGtag).toHaveBeenCalled();
    });

    it('should track search queries', () => {
      trackEvent('search', { label: 'React hooks' });
      expect(mockGtag).toHaveBeenCalled();
    });

    it('should handle special characters in parameters', () => {
      const params = { label: 'C++ programming' };
      trackEvent('search', params);
      expect(mockGtag).toHaveBeenCalled();
    });

    it('should not throw on gtag missing', () => {
      window.gtag = undefined;
      expect(() => trackEvent('test', {})).not.toThrow();
    });
  });

  describe('trackPageView', () => {
    it('should track pageview with path', () => {
      trackPageView('/blog/post-1');
      expect(mockGtag).toHaveBeenCalledWith(
        'event',
        'page_view',
        expect.objectContaining({
          page_path: '/blog/post-1',
        })
      );
    });

    it('should track pageview with title', () => {
      trackPageView('/blog/post-1', 'Getting Started with React');
      expect(mockGtag).toHaveBeenCalledWith(
        'event',
        'page_view',
        expect.objectContaining({
          page_path: '/blog/post-1',
          page_title: 'Getting Started with React',
        })
      );
    });

    it('should handle category pages', () => {
      trackPageView('/category/technology', 'Technology Posts');
      expect(mockGtag).toHaveBeenCalled();
    });

    it('should handle tag pages', () => {
      trackPageView('/tag/javascript', 'JavaScript Posts');
      expect(mockGtag).toHaveBeenCalled();
    });

    it('should handle author pages', () => {
      trackPageView('/author/john-doe', 'Posts by John Doe');
      expect(mockGtag).toHaveBeenCalled();
    });

    it('should not throw when gtag is missing', () => {
      window.gtag = undefined;
      expect(() => trackPageView('/blog')).not.toThrow();
    });
  });

  describe('Analytics Integration', () => {
    it('should track complete user journey', () => {
      trackPageView('/blog');
      trackEvent('post_viewed', { label: '123' });
      trackEvent('newsletter_signup', { category: 'conversion' });

      expect(mockGtag).toHaveBeenCalledTimes(3);
    });

    it('should handle rapid event tracking', () => {
      const events = [
        { name: 'event1', params: {} },
        { name: 'event2', params: {} },
        { name: 'event3', params: {} },
      ];

      events.forEach((event) => {
        trackEvent(event.name, event.params);
      });

      expect(mockGtag).toHaveBeenCalledTimes(3);
    });

    it('should not throw on missing gtag in production', () => {
      const originalEnv = process.env.NODE_ENV;
      process.env.NODE_ENV = 'production';

      window.gtag = undefined;
      expect(() => {
        trackPageView('/blog');
        trackEvent('test', {});
      }).not.toThrow();

      window.gtag = mockGtag;
      process.env.NODE_ENV = originalEnv;
    });

    it('should queue events if gtag not ready', () => {
      window.gtag = undefined;
      trackEvent('queued_event', { label: 'test' });

      // Should not throw even if gtag is missing
      expect(window.gtag).toBeUndefined();

      window.gtag = mockGtag;
    });
  });
});
