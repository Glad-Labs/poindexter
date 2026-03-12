import React, { useRef, useCallback, useEffect, useState } from 'react';
import { NavLink } from 'react-router-dom';
import './Sidebar.css';

const MIN_SIDEBAR_WIDTH = 200;
const MAX_SIDEBAR_WIDTH = 400;
const DEFAULT_SIDEBAR_WIDTH = 250;
const KEYBOARD_STEP = 10;

const Sidebar = () => {
  const sidebarRef = useRef(null);
  const isResizing = useRef(false);
  const [isCompressed, setIsCompressed] = useState(false);
  const [sidebarWidth, setSidebarWidth] = useState(DEFAULT_SIDEBAR_WIDTH);

  // Ensure layout is properly initialized
  useEffect(() => {
    // Just ensure CSS variables are set, let CSS handle the layout
    const currentWidth = getComputedStyle(document.documentElement)
      .getPropertyValue('--sidebar-width')
      .trim();

    if (!currentWidth || currentWidth === '') {
      document.documentElement.style.setProperty(
        '--sidebar-width',
        `${DEFAULT_SIDEBAR_WIDTH}px`
      );
    } else {
      const parsed = parseInt(currentWidth, 10);
      if (!isNaN(parsed)) setSidebarWidth(parsed);
    }
  }, []);

  const applyWidth = useCallback((newWidth) => {
    const clamped = Math.max(
      MIN_SIDEBAR_WIDTH,
      Math.min(MAX_SIDEBAR_WIDTH, newWidth)
    );
    document.documentElement.style.setProperty(
      '--sidebar-width',
      `${clamped}px`
    );
    setSidebarWidth(clamped);
  }, []);

  const handleResize = useCallback(
    (e) => {
      if (!isResizing.current) return;
      applyWidth(e.clientX);
    },
    [applyWidth]
  );

  const stopResize = useCallback(() => {
    isResizing.current = false;
    document.removeEventListener('mousemove', handleResize);
    document.removeEventListener('mouseup', stopResize);
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
  }, [handleResize]);

  const startResize = useCallback(
    (_e) => {
      isResizing.current = true;
      document.addEventListener('mousemove', handleResize);
      document.addEventListener('mouseup', stopResize);
      document.body.style.cursor = 'col-resize';
      document.body.style.userSelect = 'none';
    },
    [handleResize, stopResize]
  );

  // Keyboard handler for the resize handle — WCAG 2.1.1 compliance
  const handleResizeKeyDown = useCallback(
    (e) => {
      if (e.key === 'ArrowRight') {
        e.preventDefault();
        applyWidth(sidebarWidth + KEYBOARD_STEP);
      } else if (e.key === 'ArrowLeft') {
        e.preventDefault();
        applyWidth(sidebarWidth - KEYBOARD_STEP);
      }
    },
    [applyWidth, sidebarWidth]
  );

  return (
    <nav
      className={`sidebar ${isCompressed ? 'compressed' : ''}`}
      ref={sidebarRef}
    >
      <div className="sidebar-header">
        {/* span not h2 — heading levels in the sidebar should not precede the page h1 (WCAG 1.3.1) */}
        <span className="sidebar-title">
          {isCompressed ? 'GL' : 'Glad Labs'}
        </span>
        <button
          className="sidebar-toggle-btn"
          onClick={() => setIsCompressed(!isCompressed)}
          aria-label={isCompressed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          {isCompressed ? '→' : '←'}
        </button>
      </div>
      <div className="sidebar-nav">
        <ul>
          <li>
            <NavLink
              to="/"
              className={({ isActive }) => (isActive ? 'active' : '')}
            >
              <span className="sidebar-icon" aria-hidden="true">
                📊
              </span>
              <span className="sidebar-label">Dashboard</span>
            </NavLink>
          </li>
          <li>
            <NavLink
              to="/tasks"
              className={({ isActive }) => (isActive ? 'active' : '')}
            >
              <span className="sidebar-icon" aria-hidden="true">
                ✅
              </span>
              <span className="sidebar-label">Tasks</span>
            </NavLink>
          </li>
          <li>
            <NavLink
              to="/models"
              className={({ isActive }) => (isActive ? 'active' : '')}
            >
              <span className="sidebar-icon" aria-hidden="true">
                🤖
              </span>
              <span className="sidebar-label">Models</span>
            </NavLink>
          </li>
          <li>
            <NavLink
              to="/social"
              className={({ isActive }) => (isActive ? 'active' : '')}
            >
              <span className="sidebar-icon" aria-hidden="true">
                📱
              </span>
              <span className="sidebar-label">Social</span>
            </NavLink>
          </li>
          <li>
            <NavLink
              to="/content"
              className={({ isActive }) => (isActive ? 'active' : '')}
            >
              <span className="sidebar-icon" aria-hidden="true">
                📝
              </span>
              <span className="sidebar-label">Content</span>
            </NavLink>
          </li>
          <li>
            <NavLink
              to="/workflows"
              className={({ isActive }) => (isActive ? 'active' : '')}
            >
              <span className="sidebar-icon" aria-hidden="true">
                🔄
              </span>
              <span className="sidebar-label">Workflows</span>
            </NavLink>
          </li>
          <li>
            <NavLink
              to="/cost-metrics"
              className={({ isActive }) => (isActive ? 'active' : '')}
            >
              <span className="sidebar-icon" aria-hidden="true">
                💰
              </span>
              <span className="sidebar-label">Costs</span>
            </NavLink>
          </li>
          <li>
            <NavLink
              to="/analytics"
              className={({ isActive }) => (isActive ? 'active' : '')}
            >
              <span className="sidebar-icon" aria-hidden="true">
                📈
              </span>
              <span className="sidebar-label">Analytics</span>
            </NavLink>
          </li>
          <li>
            <NavLink
              to="/settings"
              className={({ isActive }) => (isActive ? 'active' : '')}
            >
              <span className="sidebar-icon" aria-hidden="true">
                ⚙️
              </span>
              <span className="sidebar-label">Settings</span>
            </NavLink>
          </li>
        </ul>
      </div>
      {/* Keyboard-accessible resize handle (WCAG 2.1.1) */}
      <div
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize sidebar. Use Arrow Left and Arrow Right keys to adjust width."
        aria-valuenow={sidebarWidth}
        aria-valuemin={MIN_SIDEBAR_WIDTH}
        aria-valuemax={MAX_SIDEBAR_WIDTH}
        tabIndex={0}
        className="resize-handle sidebar-resize-handle"
        onMouseDown={startResize}
        onKeyDown={handleResizeKeyDown}
      />
    </nav>
  );
};

export default Sidebar;
