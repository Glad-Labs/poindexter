# Testing Implementation Guide

Comprehensive guide for implementing and running tests for your applications.

---

## Current Testing Status

### ✅ Public Site (`web/public-site`)

- **Tests Fixed:** Jest dependency issues resolved
- **Tests Running:** All 4 component tests pass
- **Coverage:** 4 components tested (Header, Footer, Layout, PostList)
- **Test Command:** `npm test`
- **CI Command:** `npm test -- --watchAll=false`

### ❌ Strapi Backend (`cms/strapi-main`)

- **Tests:** None implemented
- **Test Infrastructure:** Not set up
- **Priority:** CRITICAL - Backend untested in production

---

## Part 1: Public Site Testing

### Test Files Structure

```
web/public-site/
├── components/
│   ├── Header.test.js          ✅ Exists
│   ├── Footer.test.js          ✅ Exists
│   ├── Layout.test.js          ✅ Exists
│   ├── PostList.test.js        ✅ Exists
│   └── [other components]      ❌ Missing tests
├── __tests__/
│   └── [page tests]            ❌ Empty
├── jest.config.js              ✅ Configured
├── jest.setup.js               ✅ Configured
└── package.json                ✅ Updated with deps
```

### Running Tests Locally

```bash
cd web/public-site

# Run all tests (watch mode)
npm test

# Run all tests once
npm test -- --watchAll=false

# Run specific test
npm test -- components/Header.test.js

# Run with coverage
npm test -- --coverage --watchAll=false

# Run tests matching pattern
npm test -- --testNamePattern="renders header"
```

### Existing Component Tests

**Example: `components/PostList.test.js`**

```javascript
import React from 'react';
import { render, screen } from '@testing-library/react';
import PostList from '../PostList';

describe('PostList', () => {
  it('renders a list of posts', () => {
    const posts = [
      {
        id: '1',
        slug: 'test-post-1',
        title: 'Test Post 1',
        date: '2024-01-01',
        excerpt: 'Test excerpt',
      },
      {
        id: '2',
        slug: 'test-post-2',
        title: 'Test Post 2',
        date: '2024-01-02',
        excerpt: 'Test excerpt 2',
      },
    ];

    render(<PostList posts={posts} />);

    expect(screen.getByText('Test Post 1')).toBeInTheDocument();
    expect(screen.getByText('Test Post 2')).toBeInTheDocument();
  });

  it('shows no posts message when empty', () => {
    render(<PostList posts={[]} />);

    expect(screen.getByText(/no posts/i)).toBeInTheDocument();
  });
});
```

### How to Add Tests

#### 1. Component Tests

Create a `components/Navigation.test.js` template:

```javascript
import React from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import Navigation from '../Navigation';

describe('Navigation', () => {
  it('renders navigation menu', () => {
    render(<Navigation />);
    expect(screen.getByRole('navigation')).toBeInTheDocument();
  });

  it('links to home page', () => {
    render(<Navigation />);
    const homeLink = screen.getByRole('link', { name: /home/i });
    expect(homeLink).toHaveAttribute('href', '/');
  });

  it('shows active link for current page', () => {
    render(<Navigation currentPage="about" />);
    const aboutLink = screen.getByRole('link', { name: /about/i });
    expect(aboutLink).toHaveClass('active');
  });
});
```

#### 2. Page Tests

Create `__tests__/pages/index.test.js`:

```javascript
import { getStaticProps } from '../../pages/index';
import { render, screen } from '@testing-library/react';
import Home from '../../pages/index';

describe('Home Page', () => {
  it('should generate static props', async () => {
    const result = await getStaticProps();

    expect(result).toHaveProperty('props');
    expect(result).toHaveProperty('revalidate');
  });

  it('renders featured posts', async () => {
    const mockPosts = [{ id: '1', title: 'Featured Post', featured: true }];

    render(<Home posts={mockPosts} />);
    expect(screen.getByText('Featured Post')).toBeInTheDocument();
  });

  it('handles empty posts gracefully', async () => {
    render(<Home posts={[]} />);
    expect(screen.getByText(/no posts/i)).toBeInTheDocument();
  });
});
```

#### 3. API Integration Tests

Create `__tests__/api/api.test.js`:

```javascript
import * as api from '../../lib/api';

describe('API Functions', () => {
  beforeEach(() => {
    global.fetch = jest.fn();
  });

  afterEach(() => {
    jest.resetAllMocks();
  });

  describe('getPosts', () => {
    it('fetches posts from API', async () => {
      const mockData = [{ id: '1', title: 'Post 1' }];

      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => mockData,
      });

      const result = await api.getPosts();
      expect(result).toEqual(mockData);
      expect(global.fetch).toHaveBeenCalled();
    });

    it('handles API errors gracefully', async () => {
      global.fetch.mockRejectedValueOnce(new Error('API Error'));

      const result = await api.getPosts();
      expect(result).toEqual([]);
    });
  });

  describe('getPostBySlug', () => {
    it('fetches single post by slug', async () => {
      const mockPost = { id: '1', slug: 'test-post', title: 'Test' };

      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => mockPost,
      });

      const result = await api.getPostBySlug('test-post');
      expect(result).toEqual(mockPost);
    });

    it('returns null for not found posts', async () => {
      global.fetch.mockResolvedValueOnce({
        ok: false,
        status: 404,
      });

      const result = await api.getPostBySlug('nonexistent');
      expect(result).toBeNull();
    });
  });
});
```

### Jest Configuration Review

**`jest.config.js`** (already correct):

```javascript
const nextJest = require('next/jest');

const createJestConfig = nextJest({
  dir: './',
});

const customJestConfig = {
  setupFilesAfterEnv: ['<rootDir>/jest.setup.js'],
  testEnvironment: 'jest-environment-jsdom',
  moduleNameMapper: {
    '^@/components/(.*)$': '<rootDir>/components/$1',
    '^@/pages/(.*)$': '<rootDir>/pages/$1',
    '^@/lib/(.*)$': '<rootDir>/lib/$1',
  },
};

module.exports = createJestConfig(customJestConfig);
```

---

## Part 2: Strapi Backend Testing

### Setup Instructions

#### Step 1: Install Testing Dependencies

```bash
cd cms/strapi-main

npm install --save-dev jest @types/jest supertest @strapi/plugin-testing
```

#### Step 2: Create `jest.config.js`

```javascript
module.exports = {
  displayName: 'strapi',
  testEnvironment: 'node',
  testMatch: ['**/__tests__/**/*.test.js', '**/?(*.)+(spec|test).js'],
  transformIgnorePatterns: ['/node_modules/'],
  collectCoverageFrom: [
    'src/**/*.js',
    '!src/index.js',
    '!src/**/*.d.ts',
    '!src/**/index.js',
  ],
  coveragePathIgnorePatterns: ['/node_modules/', 'node_modules/(?!@strapi)'],
};
```

#### Step 3: Add Test Scripts to `package.json`

```json
{
  "scripts": {
    "test": "jest --passWithNoTests",
    "test:watch": "jest --watch",
    "test:coverage": "jest --coverage",
    "test:ci": "jest --ci --coverage --maxWorkers=2"
  }
}
```

#### Step 4: Create API Test Files

**File: `src/api/__tests__/content-types.test.js`**

```javascript
describe('Content Types API', () => {
  let strapi;

  beforeAll(async () => {
    await require('../../index').default();
    strapi = global.strapi;
  });

  afterAll(async () => {
    await strapi.destroy();
  });

  describe('GET /api/posts', () => {
    it('should return all posts', async () => {
      const response = await strapi.request({
        method: 'GET',
        path: '/api/posts',
        expectedStatus: 200,
      });

      expect(response).toHaveProperty('data');
      expect(Array.isArray(response.data)).toBe(true);
    });

    it('should handle empty results', async () => {
      const response = await strapi.request({
        method: 'GET',
        path: '/api/posts?filters[id][$eq]=999',
        expectedStatus: 200,
      });

      expect(response.data).toEqual([]);
    });
  });

  describe('GET /api/posts/:id', () => {
    it('should return 404 for non-existent post', async () => {
      await strapi.request({
        method: 'GET',
        path: '/api/posts/999',
        expectedStatus: 404,
      });
    });
  });
});
```

**File: `src/api/__tests__/categories.test.js`**

```javascript
describe('Categories API', () => {
  describe('GET /api/categories', () => {
    it('should return categories with post count', async () => {
      const response = await strapi.request({
        method: 'GET',
        path: '/api/categories?populate=*',
        expectedStatus: 200,
      });

      expect(response.data).toBeDefined();
      response.data.forEach((category) => {
        expect(category).toHaveProperty('name');
        expect(category).toHaveProperty('slug');
      });
    });
  });
});
```

#### Step 5: Run Strapi Tests

```bash
cd cms/strapi-main

# Run all tests
npm test

# Run with watch mode
npm test:watch

# Run with coverage
npm test:coverage

# Run CI mode (for GitHub Actions)
npm run test:ci
```

---

## Part 3: Monorepo Test Coordination

### Run All Tests

```bash
# From root directory
npm test
```

This runs:

1. `npm test:public:ci` - Public site tests
2. `npm test:oversight:ci` - Oversight hub tests
3. `npm run test:python` - Python agent tests

### Workspace-Specific Tests

```bash
# Test only public-site
npm test --workspace=web/public-site

# Test only oversight-hub
npm test --workspace=web/oversight-hub

# Test only strapi
npm test --workspace=cms/strapi-main
```

### CI Test Command

```bash
npm run test:frontend:ci
```

Runs:

1. `npm test:public:ci` - Public site tests (without watch)
2. `npm test:oversight:ci` - Oversight hub tests (without watch)

---

## Part 4: Coverage and Quality Metrics

### View Coverage Report

```bash
cd web/public-site

# Generate coverage
npm test -- --coverage --watchAll=false

# View HTML report
open coverage/lcov-report/index.html
```

### Coverage Thresholds

Update `jest.config.js`:

```javascript
const customJestConfig = {
  // ... other config
  coverageThreshold: {
    global: {
      branches: 70,
      functions: 70,
      lines: 70,
      statements: 70,
    },
  },
};
```

### Generate Coverage Badge

Install coverage badge tool:

```bash
npm install --save-dev coverage-badge
```

Add to `package.json`:

```json
{
  "scripts": {
    "coverage:badge": "coverage-badge -o coverage.svg -b"
  }
}
```

Generate badge:

```bash
npm run coverage:badge
```

Add to README.md:

```markdown
![Coverage](./coverage.svg)
```

---

## Part 5: Best Practices

### Writing Good Tests

✅ **DO:**

- Test behavior, not implementation
- Use descriptive test names
- Keep tests focused and atomic
- Mock external dependencies
- Test error cases
- Use semantic queries (getByRole, getByText)

❌ **DON'T:**

- Test implementation details
- Create interdependent tests
- Use shallow rendering without purpose
- Skip error scenarios
- Mock everything
- Use testID as primary selector

### Test Organization

```
__tests__/
├── components/          # Component tests
├── pages/              # Page tests
├── api/                # API tests
├── utils/              # Utility function tests
└── fixtures/           # Mock data
```

### Naming Convention

```javascript
// ✅ Good
describe('PostCard component', () => {
  it('should display post title and excerpt', () => {});
  it('should link to full post when clicked', () => {});
  it('should show published date in correct format', () => {});
});

// ❌ Avoid
describe('PostCard', () => {
  it('works', () => {});
  it('renders', () => {});
  it('test 1', () => {});
});
```

---

## Troubleshooting

### Tests Timeout

**Problem:** Tests take too long to run

**Solutions:**

```javascript
// Increase timeout for specific test
jest.setTimeout(10000);

it('slow API call', async () => {
  // test
}, 10000);

// Or in jest.config.js
testTimeout: 10000,
```

### Module Not Found

**Problem:** Cannot find module in test

**Solution:** Check `jest.config.js` `moduleNameMapper`:

```javascript
moduleNameMapper: {
  '^@/components/(.*)$': '<rootDir>/components/$1',
  // Add all aliases
}
```

### Component Render Issues

**Problem:** React component won't render in tests

**Solution:** Wrap with providers:

```javascript
import { render } from '@testing-library/react';

const renderWithProviders = (component) => {
  return render(
    <Provider>
      <Router>{component}</Router>
    </Provider>
  );
};
```

---

## Next Steps

1. ✅ Run public-site tests: `npm test -- --watchAll=false`
2. ✅ Set up Strapi tests (follow Part 2 above)
3. ✅ Add component tests for remaining components
4. ✅ Add page-level tests
5. ✅ Add API integration tests
6. ✅ Set up GitHub Actions workflows
7. ✅ Add pre-commit hooks
8. ✅ Track coverage metrics
