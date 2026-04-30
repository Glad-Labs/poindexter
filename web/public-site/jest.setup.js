import '@testing-library/jest-dom';
import { useRouter } from 'next/router';

// Mock @sentry/nextjs — the real SDK accesses router.events at import time,
// which crashes in jsdom because next/router is not fully initialised.
jest.mock('@sentry/nextjs', () => ({
  init: jest.fn(),
  captureException: jest.fn(),
  captureMessage: jest.fn(),
  withScope: jest.fn((cb) => cb({ setTag: jest.fn(), setExtra: jest.fn() })),
  setUser: jest.fn(),
  setTag: jest.fn(),
  setExtra: jest.fn(),
  startSpan: jest.fn((_opts, cb) => cb()),
  metrics: { increment: jest.fn(), distribution: jest.fn() },
}));

// Mock Next.js router for testing
jest.mock('next/router', () => ({
  useRouter: jest.fn(),
}));

// Default mock implementation for useRouter
useRouter.mockImplementation(() => ({
  pathname: '/',
  route: '/',
  query: {},
  asPath: '/',
  push: jest.fn(),
  replace: jest.fn(),
  reload: jest.fn(),
  back: jest.fn(),
  forward: jest.fn(),
  prefetch: jest.fn().mockResolvedValue(undefined),
  beforePopState: jest.fn(),
  events: {
    on: jest.fn(),
    off: jest.fn(),
    emit: jest.fn(),
  },
  isFallback: false,
  isLocaleDomain: false,
  isReady: true,
  isPreview: false,
}));
