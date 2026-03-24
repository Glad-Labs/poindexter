const nextJest = require('next/jest');

const createJestConfig = nextJest({
  // Provide the path to your Next.js app to load next.config.js and .env files
  dir: './',
});

// Add any custom config to be passed to Jest
const customJestConfig = {
  setupFilesAfterEnv: ['<rootDir>/jest.setup.js'],
  moduleNameMapper: {
    // Handle module aliases (this will be automatically configured for you based on your tsconfig.json paths)
    '^@/components/(.*)$': '<rootDir>/components/$1',
    '^@/pages/(.*)$': '<rootDir>/pages/$1',
    // General catch-all for @/ → project root (matches jsconfig.json "paths")
    '^@/(.*)$': '<rootDir>/$1',
  },
  testEnvironment: 'jest-environment-jsdom',
  testPathIgnorePatterns: [
    '/node_modules/',
    '/e2e/',
    '/.next/',
    // archive/page.test.js crashes Jest workers on CI (OOM after 4 retries)
    'app/archive/__tests__/page.test.js',
  ],
  // Enforce minimum coverage thresholds. Fail CI if any threshold is missed.
  coverageThreshold: {
    global: {
      lines: 50,
      functions: 50,
      branches: 40,
      statements: 50,
    },
    // Raise thresholds for critical utility/lib paths as coverage grows
    // TODO: increase as lib/ test coverage improves
    './lib/': {
      lines: 40,
      functions: 40,
      branches: 30,
      statements: 40,
    },
  },
};

// createJestConfig is exported this way to ensure that next/jest can load the Next.js config which is async
module.exports = createJestConfig(customJestConfig);
