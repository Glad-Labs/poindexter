import React from 'react';
import { render, screen } from '@testing-library/react';
import { AffiliateDisclosure } from './AffiliateDisclosure';

test('renders provided text', () => {
  render(<AffiliateDisclosure text="Custom affiliate note." />);
  expect(screen.getByText(/Custom affiliate note\./)).toBeInTheDocument();
});

test('falls back to default when text empty', () => {
  render(<AffiliateDisclosure />);
  expect(screen.getByText(/affiliate link/i)).toBeInTheDocument();
});

test('falls back to default when text is whitespace-only', () => {
  render(<AffiliateDisclosure text="   " />);
  expect(screen.getByText(/affiliate link/i)).toBeInTheDocument();
});
