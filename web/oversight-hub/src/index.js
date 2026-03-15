import React from 'react';
import ReactDOM from 'react-dom/client';
import * as Sentry from '@sentry/react';
import './index.css';
import App from './App';
// Eagerly validate API URL config at startup — throws before any component renders
import { getApiUrl } from './config/apiConfig';
getApiUrl();

// Initialize Sentry when a DSN is configured.
// Gated so that development and staging builds without a DSN configured are
// unaffected — no network calls, no console noise.
const sentryDsn =
  process.env.VITE_SENTRY_DSN || process.env.REACT_APP_SENTRY_DSN;
if (sentryDsn) {
  Sentry.init({
    dsn: sentryDsn,
    environment: process.env.NODE_ENV,
    // Capture 100 % of transactions in development; use a lower rate in
    // production to stay within Sentry's transaction quota.
    tracesSampleRate: process.env.NODE_ENV === 'production' ? 0.1 : 1.0,
    // Ship source maps so Sentry can de-minify stack traces.
    // Source maps must be uploaded during the CI build step (Vite-compatible flow) — see docs/05-Operations.
    integrations: [Sentry.browserTracingIntegration()],
  });
}

const root = ReactDOM.createRoot(document.getElementById('root'));

// Only use StrictMode in production (ironically, to avoid double-render issues in dev that conflict with our auth initialization)
const root_element =
  process.env.NODE_ENV === 'production' ? (
    <React.StrictMode>
      <App />
    </React.StrictMode>
  ) : (
    <App />
  );

root.render(root_element);
