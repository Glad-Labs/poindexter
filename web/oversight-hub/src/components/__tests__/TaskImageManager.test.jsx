import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import TaskImageManager from '../tasks/TaskImageManager';

describe('TaskImageManager Component', () => {
  const defaultProps = {
    task: { status: 'awaiting_approval' },
    imageSource: 'pexels',
    selectedImageUrl: '',
    imageGenerating: false,
    onImageSourceChange: vi.fn(),
    onImageUrlChange: vi.fn(),
    onGenerateImage: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('should return null when task is null', () => {
    const { container } = render(
      <TaskImageManager {...defaultProps} task={null} />
    );
    expect(container.firstChild).toBeNull();
  });

  it('should return null when task status is not awaiting_approval or rejected', () => {
    const { container } = render(
      <TaskImageManager {...defaultProps} task={{ status: 'pending' }} />
    );
    expect(container.firstChild).toBeNull();
  });

  it('should render for awaiting_approval status', () => {
    render(<TaskImageManager {...defaultProps} />);
    expect(screen.getByText(/Image Management/)).toBeInTheDocument();
  });

  it('should render for rejected status', () => {
    render(
      <TaskImageManager {...defaultProps} task={{ status: 'rejected' }} />
    );
    expect(screen.getByText(/Image Management/)).toBeInTheDocument();
  });

  it('should show Pexels and SDXL source buttons', () => {
    render(<TaskImageManager {...defaultProps} />);
    expect(screen.getByText(/Pexels/)).toBeInTheDocument();
    expect(screen.getByText(/SDXL/)).toBeInTheDocument();
  });

  it('should call onImageSourceChange when Pexels button is clicked', () => {
    render(<TaskImageManager {...defaultProps} />);
    fireEvent.click(screen.getByText(/Pexels/));
    expect(defaultProps.onImageSourceChange).toHaveBeenCalledWith('pexels');
  });

  it('should call onImageSourceChange when SDXL button is clicked', () => {
    render(<TaskImageManager {...defaultProps} />);
    fireEvent.click(screen.getByText(/SDXL/));
    expect(defaultProps.onImageSourceChange).toHaveBeenCalledWith('sdxl');
  });

  it('should render image URL input', () => {
    render(<TaskImageManager {...defaultProps} />);
    expect(
      screen.getByLabelText('Image URL (or generate below)')
    ).toBeInTheDocument();
  });

  it('should call onImageUrlChange when URL input changes', () => {
    render(<TaskImageManager {...defaultProps} />);
    const urlInput = screen.getByLabelText('Image URL (or generate below)');
    fireEvent.change(urlInput, {
      target: { value: 'https://example.com/image.jpg' },
    });
    expect(defaultProps.onImageUrlChange).toHaveBeenCalledWith(
      'https://example.com/image.jpg'
    );
  });

  it('should show Generate Image button', () => {
    render(<TaskImageManager {...defaultProps} />);
    expect(screen.getByText(/Generate Image/)).toBeInTheDocument();
  });

  it('should call onGenerateImage with current source when generate is clicked', () => {
    render(<TaskImageManager {...defaultProps} />);
    fireEvent.click(screen.getByText(/Generate Image/));
    expect(defaultProps.onGenerateImage).toHaveBeenCalledWith('pexels');
  });

  it('should disable generate button when imageGenerating is true', () => {
    render(<TaskImageManager {...defaultProps} imageGenerating={true} />);
    expect(screen.getByText(/Generating/).closest('button')).toBeDisabled();
  });

  it('should show image preview when selectedImageUrl is set', () => {
    render(
      <TaskImageManager
        {...defaultProps}
        selectedImageUrl="https://example.com/test.jpg"
      />
    );
    expect(screen.getByText(/Preview/)).toBeInTheDocument();
    expect(screen.getByAltText('Selected task image preview')).toHaveAttribute(
      'src',
      'https://example.com/test.jpg'
    );
  });

  it('should not show image preview when selectedImageUrl is empty', () => {
    render(<TaskImageManager {...defaultProps} />);
    expect(screen.queryByText(/Preview/)).not.toBeInTheDocument();
  });
});
