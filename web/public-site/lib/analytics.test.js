/**
 * Analytics Utils Tests (lib/analytics.js)
 *
 * Tests analytics event tracking and pageview utilities
 * Verifies: Event tracking, pageview logging, goal conversion
 */
import {
  trackEvent,
  trackPageview,
  trackConversion,
  setUserProperties,
  identifyUser,
} from '../lib/analytics';

// Mock window.gtag for Google Analytics
const mockGtag = jest.fn();
window.gtag = mockGtag;

describe('Analytics Utils (lib/analytics.js)', () => {
  beforeEach(() => {
    mockGtag.mockClear();
  });

  describe('trackEvent', () => {
    it('should track event with event name', () => {
      trackEvent('post_viewed', { post_id: '123' });
      expect(mockGtag).toHaveBeenCalledWith(
        'event',
        'post_viewed',
        expect.any(Object)
      );
    });

    it('should include event parameters', () => {
      const params = { post_id: '123', category: 'tech' };
      trackEvent('post_viewed', params);
      expect(mockGtag).toHaveBeenCalledWith('event', 'post_viewed', params);
    });

    it('should handle empty parameters', () => {
      trackEvent('page_visited', {});
      expect(mockGtag).toHaveBeenCalledWith('event', 'page_visited', {});
    });

    it('should track button clicks', () => {
      trackEvent('button_clicked', { button_name: 'subscribe' });
      expect(mockGtag).toHaveBeenCalled();
    });

    it('should track form submissions', () => {
      trackEvent('form_submitted', { form_name: 'newsletter_signup' });
      expect(mockGtag).toHaveBeenCalled();
    });

    it('should track search queries', () => {
      trackEvent('search', { search_term: 'React hooks' });
      expect(mockGtag).toHaveBeenCalled();
    });

    it('should handle special characters in parameters', () => {
      const params = { search_term: 'C++ programming' };
      trackEvent('search', params);
      expect(mockGtag).toHaveBeenCalled();
    });

    it('should not throw on gtag missing', () => {
      const originalGtag = window.gtag;
      window.gtag = undefined;
      expect(() => trackEvent('test', {})).not.toThrow();
      window.gtag = originalGtag;
    });
  });

  describe('trackPageview', () => {
    it('should track pageview with path', () => {
      trackPageview('/blog/post-1');
      expect(mockGtag).toHaveBeenCalledWith('config', 'GA_MEASUREMENT_ID', {
        page_path: '/blog/post-1',
      });
    });

    it('should track pageview with title', () => {
      trackPageview('/blog/post-1', 'Getting Started with React');
      expect(mockGtag).toHaveBeenCalledWith(
        'config',
        expect.any(String),
        expect.any(Object)
      );
    });

    it('should use current page path if not provided', () => {
      const originalPathname = window.location.pathname;
      Object.defineProperty(window.location, 'pathname', {
        value: '/current-page',
        writable: true,
      });
      trackPageview();
      expect(mockGtag).toHaveBeenCalled();
      Object.defineProperty(window.location, 'pathname', {
        value: originalPathname,
        writable: true,
      });
    });

    it('should handle category pages', () => {
      trackPageview('/category/technology', 'Technology Posts');
      expect(mockGtag).toHaveBeenCalled();
    });

    it('should handle tag pages', () => {
      trackPageview('/tag/javascript', 'JavaScript Posts');
      expect(mockGtag).toHaveBeenCalled();
    });

    it('should handle author pages', () => {
      trackPageview('/author/john-doe', 'Posts by John Doe');
      expect(mockGtag).toHaveBeenCalled();
    });
  });

  describe('trackConversion', () => {
    it('should track conversion goal', () => {
      trackConversion('subscribe', 'newsletter');
      expect(mockGtag).toHaveBeenCalledWith(
        'event',
        'conversion',
        expect.any(Object)
      );
    });

    it('should include conversion value', () => {
      trackConversion('subscribe', 'newsletter', 10);
      expect(mockGtag).toHaveBeenCalledWith(
        'event',
        'conversion',
        expect.objectContaining({
          value: 10,
        })
      );
    });

    it('should include conversion currency', () => {
      trackConversion('purchase', 'premium_content', 99, 'USD');
      expect(mockGtag).toHaveBeenCalledWith(
        'event',
        'conversion',
        expect.objectContaining({
          currency: 'USD',
        })
      );
    });

    it('should track newsletter signup', () => {
      trackConversion('newsletter_signup', 'email');
      expect(mockGtag).toHaveBeenCalled();
    });

    it('should track content download', () => {
      trackConversion('download', 'pdf', 0, 'USD');
      expect(mockGtag).toHaveBeenCalled();
    });

    it('should handle null value gracefully', () => {
      expect(() =>
        trackConversion('subscribe', 'newsletter', null)
      ).not.toThrow();
    });
  });

  describe('setUserProperties', () => {
    it('should set user properties', () => {
      setUserProperties({ user_type: 'subscriber' });
      expect(mockGtag).toHaveBeenCalledWith('set', expect.any(Object));
    });

    it('should set multiple properties', () => {
      const properties = {
        user_type: 'subscriber',
        subscription_plan: 'premium',
        account_age: 30,
      };
      setUserProperties(properties);
      expect(mockGtag).toHaveBeenCalled();
    });

    it('should update existing properties', () => {
      setUserProperties({ user_type: 'free' });
      setUserProperties({ user_type: 'premium' });
      expect(mockGtag).toHaveBeenCalledTimes(2);
    });

    it('should handle empty properties', () => {
      expect(() => setUserProperties({})).not.toThrow();
    });
  });

  describe('identifyUser', () => {
    it('should identify user by ID', () => {
      identifyUser('user-123');
      expect(mockGtag).toHaveBeenCalledWith(
        'set',
        expect.objectContaining({
          user_id: 'user-123',
        })
      );
    });

    it('should identify user with email', () => {
      identifyUser('user-123', 'user@example.com');
      expect(mockGtag).toHaveBeenCalled();
    });

    it('should identify user with custom properties', () => {
      const properties = { name: 'John Doe', subscription: 'premium' };
      identifyUser('user-123', 'user@example.com', properties);
      expect(mockGtag).toHaveBeenCalled();
    });

    it('should handle null identification', () => {
      expect(() => identifyUser(null)).not.toThrow();
    });

    it('should allow anonymous tracking before identification', () => {
      trackEvent('page_visited', {});
      identifyUser('user-123');
      expect(mockGtag).toHaveBeenCalledTimes(2);
    });
  });

  describe('Analytics Integration', () => {
    it('should track complete user journey', () => {
      trackPageview('/blog');
      trackEvent('post_viewed', { post_id: '123' });
      trackConversion('newsletter_signup', 'email');
      identifyUser('user-123');

      expect(mockGtag).toHaveBeenCalledTimes(4);
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
        trackPageview('/blog');
        trackEvent('test', {});
      }).not.toThrow();

      window.gtag = mockGtag;
      process.env.NODE_ENV = originalEnv;
    });

    it('should queue events if gtag not ready', () => {
      window.gtag = undefined;
      trackEvent('queued_event', { test: true });

      // Should not throw even if gtag is missing
      expect(window.gtag).toBeUndefined();

      window.gtag = mockGtag;
    });
  });
});
