import '@testing-library/jest-dom/vitest';

// jsdom does not implement scrollIntoView — mock it globally
window.HTMLElement.prototype.scrollIntoView = vi.fn();

// jsdom does not implement getSelection — mock it globally
document.getSelection = vi.fn(() => ({ removeAllRanges: vi.fn() }));
