import React from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import { Settings, TaskManagement, CostMetricsDashboard } from './index';
import ExecutiveDashboard from '../components/pages/ExecutiveDashboard';
import UnifiedServicesPanel from '../components/pages/UnifiedServicesPanel';
import BlogWorkflowPage from '../pages/BlogWorkflowPage';
import AIStudio from './AIStudio';
import Content from './Content';
import PerformanceDashboard from './PerformanceDashboard';
import Login from '../pages/Login';
import AuthCallback from '../pages/AuthCallback';
import ProtectedRoute from '../components/ProtectedRoute';
import LayoutWrapper from '../components/LayoutWrapper';
import ApprovalQueue from '../components/tasks/ApprovalQueue';
import ErrorBoundary from '../components/ErrorBoundary';

function AppRoutes() {
  return (
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
  );
}

export default AppRoutes;
