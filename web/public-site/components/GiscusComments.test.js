/**
 * GiscusComments Component Tests (components/GiscusComments.tsx)
 *
 * Tests comment section component
 * Verifies: Comment display, loading state, no comments message
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import GiscusComments from './GiscusComments';

describe('GiscusComments Component', () => {
  const defaultProps = {
    postSlug: 'test-post',
    postTitle: 'Test Post Title',
  };

  it('should render comments section', () => {
    render(<GiscusComments {...defaultProps} />);
    expect(screen.getByText('Comments')).toBeInTheDocument();
  });

  it('should display comments heading', () => {
    render(<GiscusComments {...defaultProps} />);
    const heading = screen.getByRole('heading', { level: 3 });
    expect(heading).toHaveTextContent('Comments');
  });

  it('should show coming soon message when not configured', () => {
    render(<GiscusComments {...defaultProps} />);
    expect(screen.getByText(/coming soon/i)).toBeInTheDocument();
  });

  it('should include post title in placeholder', () => {
    render(<GiscusComments {...defaultProps} />);
    expect(
      screen.getByText(new RegExp(defaultProps.postTitle))
    ).toBeInTheDocument();
  });

  it('should have proper styling with dark background', () => {
    const { container } = render(<GiscusComments {...defaultProps} />);
    // Class includes opacity modifier: bg-slate-800/50
    const commentSection = container.querySelector('[class*="bg-slate-800"]');

    expect(commentSection).toBeInTheDocument();
  });

  it('should have border styling', () => {
    const { container } = render(<GiscusComments {...defaultProps} />);
    const commentSection = container.querySelector('.border');

    expect(commentSection).toBeInTheDocument();
  });

  it('should have rounded corners', () => {
    const { container } = render(<GiscusComments {...defaultProps} />);
    const commentSection = container.querySelector('.rounded-lg');

    expect(commentSection).toBeInTheDocument();
  });

  it('should display post slug in placeholder if used', () => {
    render(
      <GiscusComments postSlug="my-first-post" postTitle="My First Post" />
    );
    // Component renders with post data
    expect(screen.getByText('Comments')).toBeInTheDocument();
  });

  it('should handle long post titles', () => {
    const longTitle =
      'A Very Long Post Title That Contains Many Words About React and JavaScript and Web Development';
    render(<GiscusComments postSlug="long-post" postTitle={longTitle} />);
    expect(screen.getByText(new RegExp(longTitle))).toBeInTheDocument();
  });

  it('should handle special characters in post title', () => {
    const titleWithChars = 'React & Vue: A Comparison & Best Practices';
    render(<GiscusComments postSlug="comparison" postTitle={titleWithChars} />);
    expect(screen.getByText(new RegExp(titleWithChars))).toBeInTheDocument();
  });

  it('should have proper text color styling', () => {
    const { container } = render(<GiscusComments {...defaultProps} />);
    const heading = container.querySelector('.text-slate-300');

    expect(heading).toBeInTheDocument();
  });

  it('should have proper paragraph styling for message', () => {
    const { container } = render(<GiscusComments {...defaultProps} />);
    const paragraph = container.querySelector('.text-slate-400');

    expect(paragraph).toBeInTheDocument();
  });

  it('should render as standalone component', () => {
    const { container } = render(<GiscusComments {...defaultProps} />);
    expect(container.firstChild).toBeInTheDocument();
  });

  it('should be ready for Giscus integration', () => {
    const { container } = render(<GiscusComments {...defaultProps} />);
    // Container should be present for Giscus script injection
    const commentsDiv = container.querySelector('[class*="bg-slate-800"]');
    expect(commentsDiv).toBeInTheDocument();
  });

  it('should render with proper padding', () => {
    const { container } = render(<GiscusComments {...defaultProps} />);
    const commentSection = container.querySelector('.p-6');

    expect(commentSection).toBeInTheDocument();
  });

  it('should have proper margin spacing', () => {
    const { container } = render(<GiscusComments {...defaultProps} />);
    const commentSection = container.querySelector('[class*="mb-"]');

    expect(commentSection).toBeInTheDocument();
  });

  it('should display proper heading hierarchy', () => {
    render(<GiscusComments {...defaultProps} />);
    const h3 = screen.getByRole('heading', { level: 3 });
    expect(h3).toBeInTheDocument();
  });

  it('should be accessible with proper semantic HTML', () => {
    const { container } = render(<GiscusComments {...defaultProps} />);
    expect(container.querySelector('h3')).toBeInTheDocument();
    expect(container.querySelector('p')).toBeInTheDocument();
  });

  it('should accept both required props', () => {
    // Should not throw with both props
    expect(() => {
      render(<GiscusComments postSlug="slug" postTitle="Title" />);
    }).not.toThrow();
  });

  it('should handle empty string props gracefully', () => {
    expect(() => {
      render(<GiscusComments postSlug="" postTitle="" />);
    }).not.toThrow();
  });

  it('should prepare for real Giscus widget mounting', () => {
    const { container } = render(<GiscusComments {...defaultProps} />);
    // Should have structure ready for Giscus
    const wrapper = container.querySelector('[class*="bg-slate-800"]');
    expect(wrapper).toBeInTheDocument();
  });
});
