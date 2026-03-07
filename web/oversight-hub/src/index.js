import React from 'react';
import ReactDOM from 'react-dom/client';
import './index.css';
import App from './App';
// Eagerly validate API URL config at startup — throws before any component renders
import { getApiUrl } from './config/apiConfig';
getApiUrl();

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
