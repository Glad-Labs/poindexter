import logger from '@/lib/logger';
import React, { useState, useEffect, useCallback } from 'react';
import {
  AlertCircle,
  Filter,
  Plus,
  PlayCircle,
  CheckCircle,
  Clock,
  XCircle,
} from 'lucide-react';
import { makeRequest } from '../services/cofounderAgentClient';

const TrainingDataDashboard = () => {
  const [stats, setStats] = useState(null);
  const [datasets, setDatasets] = useState([]);
  const [trainingJobs, setTrainingJobs] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  // Filter state
  const [filters, setFilters] = useState({
    quality_min: 0.7,
    quality_max: 1.0,
    exclude_tags: 'development,test',
    success_only: false,
  });

  // Dataset creation state
  const [createDatasetName, setCreateDatasetName] = useState('production');
  const [createDatasetDesc, setCreateDatasetDesc] = useState(
    'Production-ready training data'
  );

  // Fine-tuning state
  const [fineTuneTarget, setFineTuneTarget] = useState('ollama');
  const [fineTuneDatasetPath, setFineTuneDatasetPath] = useState('');

  // Tab state
  const [activeTab, setActiveTab] = useState('data');

  const loadStats = useCallback(async () => {
    try {
      const excludeTags = filters.exclude_tags
        ? `?exclude_tags=${filters.exclude_tags}`
        : '';
      const response = await makeRequest(
        `/api/orchestrator/training/stats${excludeTags}`,
        'GET'
      );
      setStats(response);
    } catch (err) {
      logger.error('Error loading stats:', err);
    }
  }, [filters]);

  const loadDatasets = useCallback(async () => {
    try {
      const response = await makeRequest(
        '/api/orchestrator/training/datasets',
        'GET'
      );
      setDatasets(response.datasets || []);
    } catch (err) {
      logger.error('Error loading datasets:', err);
    }
  }, []);

  const loadJobs = useCallback(async () => {
    try {
      const response = await makeRequest(
        '/api/orchestrator/training/jobs',
        'GET'
      );
      setTrainingJobs(response.jobs || []);
    } catch (err) {
      logger.error('Error loading jobs:', err);
    }
  }, []);

  useEffect(() => {
    const loadAll = async () => {
      try {
        setLoading(true);
        await Promise.all([loadStats(), loadDatasets(), loadJobs()]);
        setError(null);
      } catch (err) {
        setError(err.message);
      } finally {
        setLoading(false);
      }
    };

    loadAll();
    const interval = setInterval(loadAll, 10000); // Refresh every 10s
    return () => clearInterval(interval);
  }, [loadStats, loadDatasets, loadJobs]);

  const handleTagByDate = async () => {
    try {
      const now = new Date();
      const oneMonthAgo = new Date(now.setMonth(now.getMonth() - 1));

      await makeRequest('/api/orchestrator/training/data/tag-by-date', 'POST', {
        date_after: oneMonthAgo.toISOString(),
        date_before: new Date().toISOString(),
        tags: ['development'],
      });

      alert('✅ Tagged old data as development');
      await loadStats();
    } catch (err) {
      alert(`❌ Error: ${err.message}`);
    }
  };

  const handleCreateDataset = async () => {
    try {
      setLoading(true);
      const filterObj = {
        quality_min: filters.quality_min,
        quality_max: filters.quality_max,
        exclude_tags: filters.exclude_tags.split(',').filter((t) => t.trim()),
      };

      const response = await makeRequest(
        '/api/orchestrator/training/datasets',
        'POST',
        {
          name: createDatasetName,
          description: createDatasetDesc,
          filters: filterObj,
        }
      );

      alert(
        `✅ Created dataset v${response.dataset.version} with ${response.dataset.example_count} examples`
      );
      setFineTuneDatasetPath(response.dataset.file_path);
      await loadDatasets();
    } catch (err) {
      alert(`❌ Error: ${err.message}`);
    } finally {
      setLoading(false);
    }
  };

  const handleStartFineTuning = async () => {
    try {
      if (!fineTuneDatasetPath) {
        alert('❌ Select or create a dataset first');
        return;
      }

      setLoading(true);
      const response = await makeRequest(
        '/api/orchestrator/training/fine-tune',
        'POST',
        {
          target: fineTuneTarget,
          dataset_path: fineTuneDatasetPath,
        }
      );

      if (response.success) {
        alert(
          `✅ Started ${fineTuneTarget.toUpperCase()} fine-tuning job: ${response.job.job_id}`
        );
        await loadJobs();
      } else {
        alert(`❌ Error: ${response.job.error}`);
      }
    } catch (err) {
      alert(`❌ Error: ${err.message}`);
    } finally {
      setLoading(false);
    }
  };

  const handleCancelJob = async (jobId) => {
    if (!window.confirm('Are you sure you want to cancel this job?')) return;

    try {
      await makeRequest(
        `/api/orchestrator/training/jobs/${jobId}/cancel`,
        'POST'
      );
      alert('✅ Job cancelled');
      await loadJobs();
    } catch (err) {
      alert(`❌ Error: ${err.message}`);
    }
  };

  const getStatusIcon = (status) => {
    switch (status) {
      case 'running':
        return <Clock className="w-5 h-5 text-blue-500" />;
      case 'complete':
        return <CheckCircle className="w-5 h-5 text-green-500" />;
      case 'failed':
        return <XCircle className="w-5 h-5 text-red-500" />;
      default:
        return <Clock className="w-5 h-5 text-gray-500" />;
    }
  };

  return (
    <div className="p-6 bg-gradient-to-br from-slate-50 to-slate-100 min-h-screen">
      <div className="max-w-7xl mx-auto">
        {/* Header */}
        <div className="mb-8">
          <h1 className="text-4xl font-bold text-slate-900 mb-2">
            🧠 Training Data Management
          </h1>
          <p className="text-slate-600">
            Manage training datasets, filter data, and fine-tune models
          </p>
        </div>

        {/* Error Alert */}
        {error && (
          <div className="mb-6 bg-red-50 border border-red-200 rounded-lg p-4 flex items-center gap-3">
            <AlertCircle className="w-5 h-5 text-red-600" />
            <span className="text-red-800">{error}</span>
          </div>
        )}

        {/* Tabs */}
        <div className="flex gap-4 mb-6 border-b border-slate-200">
          {[
            { id: 'data', label: '📊 Data Management', icon: '📊' },
            { id: 'datasets', label: '📦 Datasets', icon: '📦' },
            { id: 'fine-tune', label: '🚀 Fine-Tuning', icon: '🚀' },
          ].map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`px-6 py-3 font-semibold text-sm transition-colors ${
                activeTab === tab.id
                  ? 'text-blue-600 border-b-2 border-blue-600'
                  : 'text-slate-600 hover:text-slate-900'
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {/* TAB: Data Management */}
        {activeTab === 'data' && (
          <div className="space-y-6">
            {/* Statistics */}
            {stats && (
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <div className="bg-white rounded-lg shadow p-6">
                  <div className="text-3xl font-bold text-blue-600">
                    {stats.total_examples}
                  </div>
                  <div className="text-sm text-slate-600 mt-2">
                    Total Examples
                  </div>
                </div>
                <div className="bg-white rounded-lg shadow p-6">
                  <div className="text-3xl font-bold text-green-600">
                    {(stats.avg_quality_score * 100).toFixed(0)}%
                  </div>
                  <div className="text-sm text-slate-600 mt-2">Avg Quality</div>
                </div>
                <div className="bg-white rounded-lg shadow p-6">
                  <div className="text-3xl font-bold text-purple-600">
                    {(stats.success_rate * 100).toFixed(0)}%
                  </div>
                  <div className="text-sm text-slate-600 mt-2">
                    Success Rate
                  </div>
                </div>
                <div className="bg-white rounded-lg shadow p-6">
                  <div className="text-3xl font-bold text-orange-600">
                    {stats.filtered_count}
                  </div>
                  <div className="text-sm text-slate-600 mt-2">
                    Using (filtered)
                  </div>
                </div>
              </div>
            )}

            {/* Filters */}
            <div className="bg-white rounded-lg shadow p-6">
              <h2 className="text-xl font-bold mb-4 flex items-center gap-2">
                <Filter className="w-5 h-5" /> Data Filtering
              </h2>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                <div>
                  <label className="block text-sm font-medium text-slate-700 mb-2">
                    Quality Score Range
                  </label>
                  <div className="flex items-center gap-4">
                    <input
                      type="range"
                      min="0"
                      max="1"
                      step="0.05"
                      value={filters.quality_min}
                      onChange={(e) =>
                        setFilters({
                          ...filters,
                          quality_min: parseFloat(e.target.value),
                        })
                      }
                      className="flex-1"
                    />
                    <span className="text-sm font-semibold text-slate-700 min-w-24">
                      {filters.quality_min.toFixed(2)} -{' '}
                      {filters.quality_max.toFixed(2)}
                    </span>
                  </div>
                </div>

                <div>
                  <label className="block text-sm font-medium text-slate-700 mb-2">
                    Exclude Tags
                  </label>
                  <input
                    type="text"
                    placeholder="development,test"
                    value={filters.exclude_tags}
                    onChange={(e) =>
                      setFilters({ ...filters, exclude_tags: e.target.value })
                    }
                    className="w-full px-4 py-2 border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
                </div>

                <div className="flex items-center gap-3">
                  <input
                    type="checkbox"
                    id="successOnly"
                    checked={filters.success_only}
                    onChange={(e) =>
                      setFilters({ ...filters, success_only: e.target.checked })
                    }
                    className="w-4 h-4"
                  />
                  <label
                    htmlFor="successOnly"
                    className="text-sm text-slate-700"
                  >
                    Only successful executions
                  </label>
                </div>

                <button
                  onClick={loadStats}
                  disabled={loading}
                  className="px-6 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 transition-colors"
                >
                  {loading ? 'Loading...' : 'Apply Filters'}
                </button>
              </div>

              <button
                onClick={handleTagByDate}
                disabled={loading}
                className="mt-6 px-6 py-2 bg-amber-600 text-white rounded-lg hover:bg-amber-700 disabled:opacity-50 transition-colors"
              >
                Tag Old Data as Development
              </button>
            </div>

            {/* Quality Distribution */}
            {stats && (
              <div className="bg-white rounded-lg shadow p-6">
                <h2 className="text-xl font-bold mb-4">
                  📈 Quality Distribution
                </h2>
                <div className="space-y-3">
                  {Object.entries(stats.quality_distribution).map(
                    ([range, count]) => (
                      <div key={range} className="flex items-center gap-4">
                        <span className="text-sm font-medium text-slate-600 min-w-24">
                          {range}
                        </span>
                        <div className="flex-1 h-6 bg-slate-100 rounded-full overflow-hidden">
                          <div
                            className="h-full bg-gradient-to-r from-blue-400 to-blue-600 transition-all"
                            style={{
                              width: `${(count / Math.max(...Object.values(stats.quality_distribution))) * 100}%`,
                            }}
                          />
                        </div>
                        <span className="text-sm font-semibold text-slate-700 min-w-12 text-right">
                          {count}
                        </span>
                      </div>
                    )
                  )}
                </div>
              </div>
            )}

            {/* Intent Breakdown */}
            {stats && Object.keys(stats.by_intent).length > 0 && (
              <div className="bg-white rounded-lg shadow p-6">
                <h2 className="text-xl font-bold mb-4">🎯 By Intent</h2>
                <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
                  {Object.entries(stats.by_intent).map(([intent, count]) => (
                    <div key={intent} className="bg-slate-50 rounded p-3">
                      <div className="font-semibold text-blue-600">{count}</div>
                      <div className="text-xs text-slate-600 truncate">
                        {intent}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        {/* TAB: Datasets */}
        {activeTab === 'datasets' && (
          <div className="space-y-6">
            {/* Create New Dataset */}
            <div className="bg-white rounded-lg shadow p-6">
              <h2 className="text-xl font-bold mb-4 flex items-center gap-2">
                <Plus className="w-5 h-5" /> Create New Dataset
              </h2>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                <input
                  type="text"
                  placeholder="Dataset name (e.g., 'production')"
                  value={createDatasetName}
                  onChange={(e) => setCreateDatasetName(e.target.value)}
                  className="px-4 py-2 border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
                <input
                  type="text"
                  placeholder="Description"
                  value={createDatasetDesc}
                  onChange={(e) => setCreateDatasetDesc(e.target.value)}
                  className="px-4 py-2 border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>

              <button
                onClick={handleCreateDataset}
                disabled={loading || !createDatasetName}
                className="mt-6 px-8 py-3 bg-green-600 text-white rounded-lg hover:bg-green-700 disabled:opacity-50 transition-colors font-semibold"
              >
                {loading ? 'Creating...' : 'Create Dataset from Filters'}
              </button>
            </div>

            {/* Existing Datasets */}
            <div className="bg-white rounded-lg shadow p-6">
              <h2 className="text-xl font-bold mb-4">📦 Datasets</h2>

              {datasets.length === 0 ? (
                <p className="text-slate-600 text-center py-8">
                  No datasets created yet
                </p>
              ) : (
                <div className="space-y-4">
                  {datasets.map((ds) => (
                    <div
                      key={ds.id}
                      className="border border-slate-200 rounded-lg p-4 hover:bg-slate-50 transition-colors"
                    >
                      <div className="flex justify-between items-start mb-2">
                        <div>
                          <h3 className="font-bold text-lg">
                            {ds.name}{' '}
                            <span className="text-slate-500 text-sm">
                              v{ds.version}
                            </span>
                          </h3>
                          <p className="text-sm text-slate-600">
                            {ds.description}
                          </p>
                        </div>
                        <button
                          onClick={() => setFineTuneDatasetPath(ds.file_path)}
                          className="px-4 py-2 bg-blue-100 text-blue-700 rounded hover:bg-blue-200 transition-colors text-sm font-semibold"
                        >
                          Use for Fine-Tuning
                        </button>
                      </div>

                      <div className="flex gap-6 text-sm text-slate-600 mt-3">
                        <span>📊 {ds.example_count} examples</span>
                        <span>
                          ⭐ {(ds.avg_quality * 100).toFixed(1)}% avg quality
                        </span>
                        <span>
                          💾 {(ds.file_size / 1024 / 1024).toFixed(2)} MB
                        </span>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}

        {/* TAB: Fine-Tuning */}
        {activeTab === 'fine-tune' && (
          <div className="space-y-6">
            {/* Start Fine-Tuning */}
            <div className="bg-white rounded-lg shadow p-6">
              <h2 className="text-xl font-bold mb-4 flex items-center gap-2">
                <PlayCircle className="w-5 h-5" /> Start Fine-Tuning
              </h2>

              <div className="mb-6 p-4 bg-blue-50 border border-blue-200 rounded-lg">
                <p className="text-sm text-blue-900">
                  <strong>Selected dataset:</strong>{' '}
                  {fineTuneDatasetPath || 'None - select from datasets above'}
                </p>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-6">
                <div>
                  <label className="block text-sm font-medium text-slate-700 mb-3">
                    Target Model
                  </label>
                  <div className="space-y-3">
                    {[
                      {
                        value: 'ollama',
                        label: '🏠 Ollama (Local, Free, Private)',
                        desc: 'Run locally on your machine',
                      },
                      {
                        value: 'gemini',
                        label: '✨ Gemini (Google API)',
                        desc: 'Requires API key',
                      },
                      {
                        value: 'claude',
                        label: '⭐ Claude (Anthropic API)',
                        desc: 'Requires API key',
                      },
                      {
                        value: 'gpt4',
                        label: '🤖 GPT-4 (OpenAI API)',
                        desc: 'Requires API key, ~$50-200',
                      },
                    ].map((option) => (
                      <label
                        key={option.value}
                        className="flex items-start gap-3 p-3 border border-slate-200 rounded-lg cursor-pointer hover:bg-slate-50"
                      >
                        <input
                          type="radio"
                          name="target"
                          value={option.value}
                          checked={fineTuneTarget === option.value}
                          onChange={(e) => setFineTuneTarget(e.target.value)}
                          className="mt-1"
                        />
                        <div>
                          <div className="font-medium text-slate-900">
                            {option.label}
                          </div>
                          <div className="text-xs text-slate-500">
                            {option.desc}
                          </div>
                        </div>
                      </label>
                    ))}
                  </div>
                </div>

                <div>
                  <h3 className="font-semibold text-slate-900 mb-3">
                    ℹ️ Information
                  </h3>
                  <div className="space-y-3 text-sm text-slate-600">
                    {fineTuneTarget === 'ollama' && (
                      <>
                        <p>
                          ✅ <strong>Cost:</strong> $0
                        </p>
                        <p>
                          ✅ <strong>Privacy:</strong> 100% local & private
                        </p>
                        <p>
                          ✅ <strong>Speed:</strong> Depends on hardware
                        </p>
                        <p>ℹ️ Requires Ollama to be running</p>
                      </>
                    )}
                    {fineTuneTarget === 'gemini' && (
                      <>
                        <p>
                          💰 <strong>Cost:</strong> TBD by Google
                        </p>
                        <p>
                          ☁️ <strong>Privacy:</strong> Uploaded to Google
                          servers
                        </p>
                        <p>
                          ⚡ <strong>Speed:</strong> 2-4 hours typical
                        </p>
                        <p>ℹ️ Higher quality results</p>
                      </>
                    )}
                    {fineTuneTarget === 'claude' && (
                      <>
                        <p>
                          💰 <strong>Cost:</strong> $5-50
                        </p>
                        <p>
                          ☁️ <strong>Privacy:</strong> Uploaded to Anthropic
                          servers
                        </p>
                        <p>
                          ⚡ <strong>Speed:</strong> 1-3 hours typical
                        </p>
                        <p>ℹ️ Excellent quality, good value</p>
                      </>
                    )}
                    {fineTuneTarget === 'gpt4' && (
                      <>
                        <p>
                          💰 <strong>Cost:</strong> $50-200
                        </p>
                        <p>
                          ☁️ <strong>Privacy:</strong> Uploaded to OpenAI
                          servers
                        </p>
                        <p>
                          ⚡ <strong>Speed:</strong> 3-6 hours typical
                        </p>
                        <p>ℹ️ Best quality but most expensive</p>
                      </>
                    )}
                  </div>
                </div>
              </div>

              <button
                onClick={handleStartFineTuning}
                disabled={loading || !fineTuneDatasetPath}
                className="w-full px-8 py-3 bg-purple-600 text-white rounded-lg hover:bg-purple-700 disabled:opacity-50 transition-colors font-semibold text-lg"
              >
                {loading
                  ? 'Starting...'
                  : `Start ${fineTuneTarget.toUpperCase()} Fine-Tuning`}
              </button>
            </div>

            {/* Training Jobs */}
            <div className="bg-white rounded-lg shadow p-6">
              <h2 className="text-xl font-bold mb-4">⏱️ Training Jobs</h2>

              {trainingJobs.length === 0 ? (
                <p className="text-slate-600 text-center py-8">
                  No training jobs yet
                </p>
              ) : (
                <div className="space-y-4">
                  {trainingJobs.map((job) => (
                    <div
                      key={job.job_id}
                      className="border border-slate-200 rounded-lg p-4 flex items-start justify-between hover:bg-slate-50 transition-colors"
                    >
                      <div className="flex-1">
                        <div className="flex items-center gap-3 mb-2">
                          {getStatusIcon(job.status)}
                          <h3 className="font-bold text-lg">
                            {job.target.toUpperCase()} -{' '}
                            {job.status.toUpperCase()}
                          </h3>
                        </div>
                        <p className="text-sm text-slate-600 mb-2">
                          Job ID: {job.job_id}
                        </p>
                        <p className="text-sm text-slate-600">
                          Started: {new Date(job.start_time).toLocaleString()}
                        </p>

                        {job.status === 'complete' && job.model_name && (
                          <p className="text-sm text-green-700 font-semibold mt-2">
                            ✅ Model: {job.model_name}
                          </p>
                        )}

                        {job.status === 'failed' && (
                          <p className="text-sm text-red-700 font-semibold mt-2">
                            ❌ {job.error || 'Training failed'}
                          </p>
                        )}

                        {job.progress && (
                          <p className="text-sm text-slate-600 mt-2">
                            {job.progress}
                          </p>
                        )}
                      </div>

                      {job.status === 'running' && (
                        <button
                          onClick={() => handleCancelJob(job.job_id)}
                          className="px-4 py-2 bg-red-100 text-red-700 rounded hover:bg-red-200 transition-colors text-sm font-semibold"
                        >
                          Cancel
                        </button>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default TrainingDataDashboard;
