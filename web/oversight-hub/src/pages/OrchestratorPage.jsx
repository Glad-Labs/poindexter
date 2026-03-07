import logger from '@/lib/logger';
import React, { useState, useEffect } from 'react';
import {
  AlertCircle,
  RefreshCw,
  Send,
  CheckCircle,
  XCircle,
  Clock,
  BarChart3,
  Brain,
  Download,
  Play,
  Pause,
} from 'lucide-react';
import { makeRequest } from '../services/cofounderAgentClient';
import { unifiedStatusService } from '../services/unifiedStatusService';

const OrchestratorPage = () => {
  const [userRequest, setUserRequest] = useState('');
  const [orchestrations, setOrchestrations] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [approvalMode, setApprovalMode] = useState(false);
  const [learningPatterns, setLearningPatterns] = useState(null);
  const [executionStats, setExecutionStats] = useState(null);

  const statusColors = {
    pending_approval: 'bg-yellow-100 text-yellow-800',
    approved: 'bg-blue-100 text-blue-800',
    executing: 'bg-purple-100 text-purple-800',
    completed: 'bg-green-100 text-green-800',
    failed: 'bg-red-100 text-red-800',
  };

  const statusIcons = {
    pending_approval: <Clock className="w-4 h-4" />,
    approved: <CheckCircle className="w-4 h-4" />,
    executing: <RefreshCw className="w-4 h-4 animate-spin" />,
    completed: <CheckCircle className="w-4 h-4" />,
    failed: <XCircle className="w-4 h-4" />,
  };

  useEffect(() => {
    loadOrchestrations();
    loadExecutionStats();
    const interval = setInterval(() => {
      loadOrchestrations();
      loadExecutionStats();
    }, 5000);
    return () => clearInterval(interval);
  }, []);

  const loadOrchestrations = async () => {
    try {
      const response = await makeRequest(
        '/api/orchestrator/executions?limit=50',
        'GET'
      );
      setOrchestrations(response.executions || []);
      setError(null);
    } catch (err) {
      setError(err.message);
      logger.error('Error loading orchestrations:', err);
    }
  };

  const loadExecutionStats = async () => {
    try {
      const response = await makeRequest('/api/orchestrator/stats', 'GET');
      setExecutionStats(response.stats);
    } catch (err) {
      logger.error('Error loading stats:', err);
    }
  };

  const handleProcessRequest = async (_e) => {
    _e.preventDefault();
    if (!userRequest.trim()) return;

    try {
      setLoading(true);
      const _response = await makeRequest('/api/orchestrator/process', 'POST', {
        user_request: userRequest,
      });

      alert('✅ Request submitted for orchestration');
      setUserRequest('');
      await loadOrchestrations();
    } catch (err) {
      alert(`❌ Error: ${err.message}`);
    } finally {
      setLoading(false);
    }
  };

  const handleApprove = async (executionId) => {
    try {
      await unifiedStatusService.approve(
        executionId,
        'Approved via OrchestratorPage'
      );
      alert('✅ Execution approved');
      await loadOrchestrations();
    } catch (err) {
      alert(`❌ Error: ${err.message}`);
    }
  };

  const handleReject = async (executionId) => {
    const reason = window.prompt('Reason for rejection:');
    if (!reason) return;

    try {
      await unifiedStatusService.reject(executionId, reason);
      alert('✅ Execution rejected');
      await loadOrchestrations();
    } catch (err) {
      alert(`❌ Error: ${err.message}`);
    }
  };

  const handleExportResults = async (executionId) => {
    try {
      const response = await makeRequest(
        `/api/orchestrator/executions/${executionId}/export`,
        'GET'
      );
      const dataStr = JSON.stringify(response, null, 2);
      const dataBlob = new Blob([dataStr], { type: 'application/json' });
      const url = URL.createObjectURL(dataBlob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `execution_${executionId}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      alert(`❌ Error: ${err.message}`);
    }
  };

  const handleViewLearnings = async (executionId) => {
    try {
      const response = await makeRequest(
        `/api/orchestrator/executions/${executionId}/learnings`,
        'GET'
      );
      setLearningPatterns(response);
    } catch (err) {
      alert(`❌ Error: ${err.message}`);
    }
  };

  return (
    <div className="p-6 bg-gradient-to-br from-slate-50 to-slate-100 min-h-screen">
      <div className="max-w-7xl mx-auto">
        {/* Header */}
        <div className="mb-8">
          <h1 className="text-4xl font-bold text-slate-900 mb-2">
            🧠 Orchestrator Dashboard
          </h1>
          <p className="text-slate-600">
            Process tasks through AI orchestration with approval workflow
          </p>
        </div>

        {/* Error Alert */}
        {error && (
          <div className="mb-6 bg-red-50 border border-red-200 rounded-lg p-4 flex items-center gap-3">
            <AlertCircle className="w-5 h-5 text-red-600" />
            <span className="text-red-800">{error}</span>
          </div>
        )}

        {/* Statistics */}
        {executionStats && (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
            <div className="bg-white rounded-lg shadow p-4 hover:shadow-lg transition-shadow">
              <div className="text-2xl font-bold text-slate-900">
                {executionStats.total_executions}
              </div>
              <div className="text-sm text-slate-600 mt-1">
                Total Executions
              </div>
            </div>
            <div className="bg-blue-50 rounded-lg shadow p-4 hover:shadow-lg transition-shadow border border-blue-200">
              <div className="text-2xl font-bold text-blue-700">
                {executionStats.success_rate}%
              </div>
              <div className="text-sm text-blue-600 mt-1">Success Rate</div>
            </div>
            <div className="bg-purple-50 rounded-lg shadow p-4 hover:shadow-lg transition-shadow border border-purple-200">
              <div className="text-2xl font-bold text-purple-700">
                {executionStats.avg_execution_time}s
              </div>
              <div className="text-sm text-purple-600 mt-1">
                Avg Execution Time
              </div>
            </div>
            <div className="bg-green-50 rounded-lg shadow p-4 hover:shadow-lg transition-shadow border border-green-200">
              <div className="text-2xl font-bold text-green-700">
                {executionStats.patterns_learned}
              </div>
              <div className="text-sm text-green-600 mt-1">
                Patterns Learned
              </div>
            </div>
          </div>
        )}

        {/* Submit Request Form */}
        <div className="bg-white rounded-lg shadow p-6 mb-6">
          <h2 className="text-xl font-bold text-slate-900 mb-4 flex items-center gap-2">
            <Send className="w-5 h-5 text-blue-600" />
            Submit Request for Orchestration
          </h2>
          <form onSubmit={handleProcessRequest} className="space-y-4">
            <textarea
              value={userRequest}
              onChange={(e) => setUserRequest(e.target.value)}
              placeholder="Describe what you want the orchestrator to handle. Example: 'Draft a Twitter thread about machine learning trends and post it'"
              className="w-full px-4 py-3 border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none"
              rows="4"
            />
            <div className="flex gap-2">
              <button
                type="submit"
                disabled={loading || !userRequest.trim()}
                className="px-6 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 transition-colors font-medium flex items-center gap-2"
              >
                <Send className="w-4 h-4" />
                {loading ? 'Processing...' : 'Submit to Orchestrator'}
              </button>
              <button
                type="button"
                onClick={loadOrchestrations}
                className="px-4 py-2 bg-slate-200 text-slate-900 rounded-lg hover:bg-slate-300 transition-colors font-medium flex items-center gap-2"
              >
                <RefreshCw className="w-4 h-4" />
                Refresh
              </button>
            </div>
          </form>
        </div>

        {/* Approval Workflow Toggle */}
        <div className="bg-amber-50 border border-amber-200 rounded-lg p-4 mb-6 flex items-center justify-between">
          <div>
            <h3 className="font-semibold text-amber-900">Approval Workflow</h3>
            <p className="text-sm text-amber-800">
              Require manual approval before executing plans
            </p>
          </div>
          <button
            onClick={() => setApprovalMode(!approvalMode)}
            className={`px-4 py-2 rounded-lg font-medium flex items-center gap-2 transition-colors ${
              approvalMode
                ? 'bg-amber-600 text-white hover:bg-amber-700'
                : 'bg-slate-200 text-slate-900 hover:bg-slate-300'
            }`}
          >
            {approvalMode ? (
              <Pause className="w-4 h-4" />
            ) : (
              <Play className="w-4 h-4" />
            )}
            {approvalMode ? 'Enabled' : 'Disabled'}
          </button>
        </div>

        {/* Orchestrations List */}
        <div className="bg-white rounded-lg shadow overflow-hidden">
          <div className="p-4 bg-slate-50 border-b border-slate-200">
            <h2 className="text-lg font-bold text-slate-900 flex items-center gap-2">
              <BarChart3 className="w-5 h-5 text-blue-600" />
              Execution History
            </h2>
          </div>

          {orchestrations.length === 0 ? (
            <div className="p-8 text-center text-slate-500">
              <Brain className="w-12 h-12 mx-auto mb-3 opacity-40" />
              <p>No orchestrations yet</p>
            </div>
          ) : (
            <div className="space-y-4 p-4">
              {orchestrations.map((orch) => (
                <div
                  key={orch.id}
                  className="border border-slate-200 rounded-lg p-4 hover:shadow-md transition-shadow"
                >
                  {/* Execution Header */}
                  <div className="flex items-start justify-between mb-3">
                    <div className="flex-1">
                      <div className="flex items-center gap-2 mb-2">
                        <span
                          className={`inline-flex items-center gap-2 px-3 py-1 rounded-full text-xs font-semibold ${
                            statusColors[orch.status]
                          }`}
                        >
                          {statusIcons[orch.status]}
                          {orch.status.replace('_', ' ').toUpperCase()}
                        </span>
                        <span className="text-xs text-slate-500">
                          {orch.id.slice(0, 12)}...
                        </span>
                      </div>
                      <p className="text-sm font-semibold text-slate-900">
                        {orch.user_request}
                      </p>
                      <p className="text-xs text-slate-600 mt-1">
                        Created: {new Date(orch.created_at).toLocaleString()}
                      </p>
                    </div>
                    <div className="text-right">
                      <p className="text-xs font-semibold text-slate-600">
                        {orch.completion_time && `${orch.completion_time}ms`}
                      </p>
                    </div>
                  </div>

                  {/* Execution Plan */}
                  {orch.execution_plan && (
                    <div className="mb-3 p-3 bg-slate-50 rounded-lg">
                      <p className="text-xs font-semibold text-slate-700 mb-2">
                        Orchestration Plan:
                      </p>
                      <p className="text-sm text-slate-700 whitespace-pre-wrap line-clamp-3">
                        {orch.execution_plan}
                      </p>
                    </div>
                  )}

                  {/* Execution Result */}
                  {orch.execution_result && (
                    <div className="mb-3 p-3 bg-green-50 rounded-lg border border-green-200">
                      <p className="text-xs font-semibold text-green-700 mb-2">
                        Result:
                      </p>
                      <p className="text-sm text-green-700 line-clamp-3">
                        {typeof orch.execution_result === 'string'
                          ? orch.execution_result
                          : JSON.stringify(orch.execution_result).slice(0, 200)}
                      </p>
                    </div>
                  )}

                  {/* Error Message */}
                  {orch.error_message && (
                    <div className="mb-3 p-3 bg-red-50 rounded-lg border border-red-200">
                      <p className="text-xs font-semibold text-red-700 mb-2">
                        Error:
                      </p>
                      <p className="text-sm text-red-700">
                        {orch.error_message}
                      </p>
                    </div>
                  )}

                  {/* Actions */}
                  <div className="flex flex-wrap gap-2">
                    {orch.status === 'pending_approval' && approvalMode && (
                      <>
                        <button
                          onClick={() => handleApprove(orch.id)}
                          className="px-3 py-1 bg-green-100 text-green-700 rounded text-xs font-semibold hover:bg-green-200 transition-colors"
                        >
                          ✓ Approve
                        </button>
                        <button
                          onClick={() => handleReject(orch.id)}
                          className="px-3 py-1 bg-red-100 text-red-700 rounded text-xs font-semibold hover:bg-red-200 transition-colors"
                        >
                          ✕ Reject
                        </button>
                      </>
                    )}
                    <button
                      onClick={() => handleViewLearnings(orch.id)}
                      className="px-3 py-1 bg-blue-100 text-blue-700 rounded text-xs font-semibold hover:bg-blue-200 transition-colors"
                    >
                      <Brain className="w-3 h-3 inline mr-1" />
                      Learnings
                    </button>
                    <button
                      onClick={() => handleExportResults(orch.id)}
                      className="px-3 py-1 bg-purple-100 text-purple-700 rounded text-xs font-semibold hover:bg-purple-200 transition-colors"
                    >
                      <Download className="w-3 h-3 inline mr-1" />
                      Export
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Learning Patterns Modal */}
        {learningPatterns && (
          <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center p-4 z-50">
            <div className="bg-white rounded-lg shadow-xl max-w-2xl w-full max-h-96 overflow-auto">
              <div className="p-6">
                <div className="flex items-center justify-between mb-4">
                  <h3 className="text-lg font-bold text-slate-900 flex items-center gap-2">
                    <Brain className="w-5 h-5 text-blue-600" />
                    Learning Patterns Discovered
                  </h3>
                  <button
                    onClick={() => setLearningPatterns(null)}
                    className="text-slate-500 hover:text-slate-700"
                  >
                    ✕
                  </button>
                </div>
                <pre className="bg-slate-50 p-4 rounded-lg text-xs overflow-x-auto text-slate-700">
                  {JSON.stringify(learningPatterns, null, 2)}
                </pre>
              </div>
            </div>
          </div>
        )}

        {/* Info Section */}
        <div className="mt-6 grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="bg-blue-50 border border-blue-200 rounded-lg p-4">
            <h3 className="font-semibold text-blue-900 mb-2">
              📋 How It Works
            </h3>
            <ul className="text-sm text-blue-800 space-y-1 list-disc list-inside">
              <li>Submit your request (what you want done)</li>
              <li>Orchestrator creates execution plan with subtasks</li>
              <li>You can approve or reject the plan</li>
              <li>Plan executes with real-time status updates</li>
              <li>Results and learnings are saved for training</li>
            </ul>
          </div>

          <div className="bg-purple-50 border border-purple-200 rounded-lg p-4">
            <h3 className="font-semibold text-purple-900 mb-2">
              🧠 Learning System
            </h3>
            <ul className="text-sm text-purple-800 space-y-1 list-disc list-inside">
              <li>Each execution generates training data</li>
              <li>Success patterns are identified automatically</li>
              <li>Learnings improve future orchestrations</li>
              <li>View learnings for any completed execution</li>
              <li>Export data for fine-tuning custom models</li>
            </ul>
          </div>
        </div>
      </div>
    </div>
  );
};

export default OrchestratorPage;
