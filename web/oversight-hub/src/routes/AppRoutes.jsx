import React, { lazy, Suspense } from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import Login from '../pages/Login';
import AuthCallback from '../pages/AuthCallback';
import ProtectedRoute from '../components/ProtectedRoute';
import LayoutWrapper from '../components/LayoutWrapper';
import ErrorBoundary from '../components/ErrorBoundary';

// Lazy-load route pages to reduce initial bundle size (#1211)
const ExecutiveDashboard = lazy(
  () => import('../components/pages/ExecutiveDashboard')
);
const UnifiedServicesPanel = lazy(
  () => import('../components/pages/UnifiedServicesPanel')
);
const BlogWorkflowPage = lazy(() => import('../pages/BlogWorkflowPage'));
const AIStudio = lazy(() => import('./AIStudio'));
const Content = lazy(() => import('./Content'));
const PerformanceDashboard = lazy(() => import('./PerformanceDashboard'));
const TaskManagement = lazy(() => import('./TaskManagement'));
const Settings = lazy(() => import('./Settings'));
const CostMetricsDashboard = lazy(() => import('./CostMetricsDashboard'));
const ApprovalQueue = lazy(() => import('../components/tasks/ApprovalQueue'));

function LoadingFallback() {
  return (
    <div
      style={{
        display: 'flex',
        justifyContent: 'center',
        alignItems: 'center',
        height: '50vh',
        color: '#888',
      }}
    >
      Loading...
    </div>
  );
}

function AppRoutes() {
  return (
    <Suspense fallback={<LoadingFallback />}>
      <Routes>
        {/* Public Routes */}
        <Route
          path="/login"
          element={
            <ErrorBoundary name="Login">
              <Login />
            </ErrorBoundary>
          }
        />
        <Route
          path="/auth/callback"
          element={
            <ErrorBoundary name="AuthCallback">
              <AuthCallback />
            </ErrorBoundary>
          }
        />

        {/* Protected Routes with Layout */}
        <Route
          path="/"
          element={
            <ProtectedRoute>
              <LayoutWrapper>
                <ErrorBoundary name="ExecutiveDashboard">
                  <ExecutiveDashboard />
                </ErrorBoundary>
              </LayoutWrapper>
            </ProtectedRoute>
          }
        />
        <Route
          path="/tasks"
          element={
            <ProtectedRoute>
              <LayoutWrapper>
                <ErrorBoundary name="TaskManagement">
                  <TaskManagement />
                </ErrorBoundary>
              </LayoutWrapper>
            </ProtectedRoute>
          }
        />
        <Route
          path="/content"
          element={
            <ProtectedRoute>
              <LayoutWrapper>
                <ErrorBoundary name="Content">
                  <Content />
                </ErrorBoundary>
              </LayoutWrapper>
            </ProtectedRoute>
          }
        />
        <Route
          path="/approvals"
          element={
            <ProtectedRoute>
              <LayoutWrapper>
                <ErrorBoundary name="ApprovalQueue">
                  <ApprovalQueue />
                </ErrorBoundary>
              </LayoutWrapper>
            </ProtectedRoute>
          }
        />
        <Route
          path="/ai"
          element={
            <ProtectedRoute>
              <LayoutWrapper>
                <ErrorBoundary name="AIStudio">
                  <AIStudio />
                </ErrorBoundary>
              </LayoutWrapper>
            </ProtectedRoute>
          }
        />
        <Route
          path="/settings"
          element={
            <ProtectedRoute>
              <LayoutWrapper>
                <ErrorBoundary name="Settings">
                  <Settings />
                </ErrorBoundary>
              </LayoutWrapper>
            </ProtectedRoute>
          }
        />
        <Route
          path="/costs"
          element={
            <ProtectedRoute>
              <LayoutWrapper>
                <ErrorBoundary name="CostMetrics">
                  <CostMetricsDashboard />
                </ErrorBoundary>
              </LayoutWrapper>
            </ProtectedRoute>
          }
        />
        <Route
          path="/performance"
          element={
            <ProtectedRoute>
              <LayoutWrapper>
                <ErrorBoundary name="PerformanceDashboard">
                  <PerformanceDashboard />
                </ErrorBoundary>
              </LayoutWrapper>
            </ProtectedRoute>
          }
        />
        <Route
          path="/workflows"
          element={
            <ProtectedRoute>
              <LayoutWrapper>
                <ErrorBoundary name="BlogWorkflow">
                  <BlogWorkflowPage />
                </ErrorBoundary>
              </LayoutWrapper>
            </ProtectedRoute>
          }
        />
        <Route
          path="/services"
          element={
            <ProtectedRoute>
              <LayoutWrapper>
                <ErrorBoundary name="UnifiedServices">
                  <UnifiedServicesPanel />
                </ErrorBoundary>
              </LayoutWrapper>
            </ProtectedRoute>
          }
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Suspense>
  );
}

export default AppRoutes;
