import React from 'react';
import { render, screen } from '@testing-library/react';
import { InBrief } from './InBrief';

test('shows the summary under an In brief label', () => {
  render(<InBrief summary="The RTX 5090 runs 70B models at 4-bit." />);
  expect(
    screen.getByRole('complementary', { name: 'In brief' })
  ).toBeInTheDocument();
  expect(
    screen.getByText('The RTX 5090 runs 70B models at 4-bit.')
  ).toBeInTheDocument();
});

test('renders nothing without a summary', () => {
  const { container } = render(<InBrief summary="  " />);
  expect(container).toBeEmptyDOMElement();
  const { container: none } = render(<InBrief />);
  expect(none).toBeEmptyDOMElement();
});
