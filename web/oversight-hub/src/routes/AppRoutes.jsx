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

function AppRoutes() {
  return (
    <Routes>
      {/* Public Routes */}
      <Route path="/login" element={<Login />} />
      <Route path="/auth/callback" element={<AuthCallback />} />

      {/* Protected Routes with Layout */}
      <Route
        path="/"
        element={
          <ProtectedRoute>
            <LayoutWrapper>
              <ExecutiveDashboard />
            </LayoutWrapper>
          </ProtectedRoute>
        }
      />
      <Route
        path="/tasks"
        element={
          <ProtectedRoute>
            <LayoutWrapper>
              <TaskManagement />
            </LayoutWrapper>
          </ProtectedRoute>
        }
      />
      <Route
        path="/content"
        element={
          <ProtectedRoute>
            <LayoutWrapper>
              <Content />
            </LayoutWrapper>
          </ProtectedRoute>
        }
      />
      <Route
        path="/approvals"
        element={
          <ProtectedRoute>
            <LayoutWrapper>
              <ApprovalQueue />
            </LayoutWrapper>
          </ProtectedRoute>
        }
      />
      <Route
        path="/ai"
        element={
          <ProtectedRoute>
            <LayoutWrapper>
              <AIStudio />
            </LayoutWrapper>
          </ProtectedRoute>
        }
      />
      <Route
        path="/settings"
        element={
          <ProtectedRoute>
            <LayoutWrapper>
              <Settings />
            </LayoutWrapper>
          </ProtectedRoute>
        }
      />
      <Route
        path="/costs"
        element={
          <ProtectedRoute>
            <LayoutWrapper>
              <CostMetricsDashboard />
            </LayoutWrapper>
          </ProtectedRoute>
        }
      />
      <Route
        path="/performance"
        element={
          <ProtectedRoute>
            <LayoutWrapper>
              <PerformanceDashboard />
            </LayoutWrapper>
          </ProtectedRoute>
        }
      />
      <Route
        path="/workflows"
        element={
          <ProtectedRoute>
            <LayoutWrapper>
              <BlogWorkflowPage />
            </LayoutWrapper>
          </ProtectedRoute>
        }
      />
      <Route
        path="/services"
        element={
          <ProtectedRoute>
            <LayoutWrapper>
              <UnifiedServicesPanel />
            </LayoutWrapper>
          </ProtectedRoute>
        }
      />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export default AppRoutes;
