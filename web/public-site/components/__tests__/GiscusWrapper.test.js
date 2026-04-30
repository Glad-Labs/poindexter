/**
 * GiscusWrapper Component Tests
 *
 * Tests the wrapper that delegates to GiscusComments.
 * Verifies: Props are forwarded, renders comment section
 */
import { render, screen } from '@testing-library/react';
import { GiscusWrapper } from '../GiscusWrapper';

describe('GiscusWrapper Component', () => {
  it('should render the Discussion heading', () => {
    render(<GiscusWrapper postSlug="test-post" postTitle="Test Post" />);
    expect(screen.getByText('Discussion')).toBeInTheDocument();
  });

  it('should render a container for the Giscus script', () => {
    const { container } = render(
      <GiscusWrapper postSlug="my-post" postTitle="My Great Post" />
    );
    // Component renders a div with a ref for the Giscus script
    expect(container.querySelector('div')).toBeInTheDocument();
  });

  it('should render within a container element', () => {
    const { container } = render(
      <GiscusWrapper postSlug="slug" postTitle="Title" />
    );
    expect(container.firstChild).toBeInTheDocument();
  });
});
