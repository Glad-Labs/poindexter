import logger from '@/lib/logger';
/**
 * AIStudio.jsx
 *
 * Consolidated AI Management Dashboard combining:
 * - Model Management & Testing (Ollama, OpenAI, Anthropic, Google)
 * - Training Data Management (dataset creation, fine-tuning)
 *
 * Provides unified interface for all AI/ML operations
 */

import React, { useState, useEffect, useCallback, useRef } from 'react';
import './AIStudio.css';
import { makeRequest } from '../services/cofounderAgentClient';

function AIStudio() {
  // ============================================================================
  // MODELS TAB STATE
  // ============================================================================

  const FALLBACK_MODELS = [
    {
      id: 1,
      name: 'gpt-4',
      displayName: 'gpt-4 (openai)',
      provider: 'openai',
      status: 'Active',
      isFree: false,
      size: 'unknown',
      description: 'Model from openai',
    },
    {
      id: 2,
      name: 'claude-3-opus',
      displayName: 'claude-3-opus (anthropic)',
      provider: 'anthropic',
      status: 'Active',
      isFree: false,
      size: 'unknown',
      description: 'Model from anthropic',
    },
    {
      id: 3,
      name: 'gpt-3.5-turbo',
      displayName: 'gpt-3.5-turbo (openai)',
      provider: 'openai',
      status: 'Active',
      isFree: false,
      size: 'unknown',
      description: 'Model from openai',
    },
    {
      id: 4,
      name: 'llama3',
      displayName: 'llama3 (ollama)',
      provider: 'ollama',
      status: 'Active',
      isFree: true,
      size: '7B-13B',
      description: 'Model from ollama',
    },
  ];

  const [models, setModels] = useState([]);
  const [modelsLoading, setModelsLoading] = useState(true);
  const [ollamaModels, setOllamaModels] = useState([]);
  const [selectedModel, setSelectedModel] = useState('');
  const [testPrompt, setTestPrompt] = useState('What is AI?');
  const [temperature, setTemperature] = useState(0.7);
  const [maxTokens, setMaxTokens] = useState(500);
  const [testLoading, setTestLoading] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const [testError, setTestError] = useState(null);
  const [testHistory, setTestHistory] = useState([]);

  // ============================================================================
  // TRAINING TAB STATE
  // ============================================================================

  const [stats, setStats] = useState(null);
  const [datasets, setDatasets] = useState([]);
  const [trainingJobs, setTrainingJobs] = useState([]);
  const [trainingLoading, setTrainingLoading] = useState(false);
  const [trainingError, setTrainingError] = useState(null);

  const [filters] = useState({
    quality_min: 0.7,
    quality_max: 1.0,
    exclude_tags: 'development,test',
    success_only: false,
  });

  // Unused variables commented out for future implementation
  // const [createDatasetName, setCreateDatasetName] = useState('production');
  // const [createDatasetDesc, setCreateDatasetDesc] = useState('Production-ready training data');
  // const [fineTuneTarget, setFineTuneTarget] = useState('ollama');
  // const [fineTuneDatasetPath, setFineTuneDatasetPath] = useState('');

  // ============================================================================
  // MAIN TAB STATE
  // ============================================================================

  const [activeTab, setActiveTab] = useState('models'); // models, test, training, history

  // ============================================================================
  // EFFECTS
  // ============================================================================

  // Track if we've already fetched Ollama models
  const hasInitializedRef = useRef(false);

  // ============================================================================
  // TRAINING DATA FUNCTIONS (MUST BE BEFORE useEffect THAT CALLS THEM)
  // ============================================================================

  const loadTrainingStats = useCallback(async () => {
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

  const loadTrainingAll = useCallback(async () => {
    try {
      setTrainingLoading(true);
      setTrainingError(null);
      await Promise.all([loadTrainingStats(), loadDatasets(), loadJobs()]);
    } catch (err) {
      setTrainingError(err.message);
    } finally {
      setTrainingLoading(false);
    }
  }, [loadTrainingStats, loadDatasets, loadJobs]);

  // ============================================================================
  // EFFECTS
  // ============================================================================

  // Fetch Ollama models on component mount
  useEffect(() => {
    if (hasInitializedRef.current) return;

    const fetchOllamaModels = async () => {
      try {
        // Use centralized config with fallback to localhost:11434 for local Ollama
        const ollamaUrl =
          import.meta.env.VITE_OLLAMA_URL || 'http://localhost:11434';
        const response = await fetch(`${ollamaUrl}/api/tags`);
        if (!response.ok) throw new Error('Failed to fetch Ollama models');

        const data = await response.json();
        const modelsArray = data.models || [];

        setOllamaModels(modelsArray);
        if (modelsArray.length > 0 && !selectedModel) {
          setSelectedModel(modelsArray[0].name);
        }
      } catch (error) {
        logger.error('Error fetching Ollama models:', error);
        setOllamaModels([]);
      }
    };
    hasInitializedRef.current = true;
    fetchOllamaModels();
  }, [selectedModel]);

  // Fetch cloud provider models from API
  useEffect(() => {
    const fetchModels = async () => {
      try {
        setModelsLoading(true);
        const response = await makeRequest('/api/models/available', 'GET');
        const fetched = response?.models || response;
        if (Array.isArray(fetched) && fetched.length > 0) {
          setModels(
            fetched.map((m, idx) => ({
              id: m.id || idx + 1,
              name: m.name || m.model_name || 'Unknown',
              displayName: m.displayName || m.name || 'Unknown',
              provider: m.provider || 'Unknown',
              status: m.status || 'Active',
              isFree: m.isFree ?? false,
              size: m.size || 'unknown',
              description: m.description || '',
            }))
          );
        } else {
          setModels(FALLBACK_MODELS);
        }
      } catch (err) {
        logger.warn(
          'Could not fetch models from API, using fallback data:',
          err
        );
        setModels(FALLBACK_MODELS);
      } finally {
        setModelsLoading(false);
      }
    };
    fetchModels();
  }, []);

  // Load training data

  useEffect(() => {
    if (activeTab === 'training') {
      loadTrainingAll();
    }
  }, [activeTab, filters, loadTrainingAll]);

  // ============================================================================
  // MODEL TESTING FUNCTIONS
  // ============================================================================

  const runModelTest = async () => {
    if (!selectedModel || !testPrompt.trim()) {
      setTestError('Please select a model and enter a prompt');
      return;
    }

    setTestLoading(true);
    setTestError(null);
    const startTime = Date.now();

    try {
      // Use centralized config with fallback to localhost:11434 for local Ollama
      const ollamaUrl =
        import.meta.env.VITE_OLLAMA_URL || 'http://localhost:11434';
      const response = await fetch(`${ollamaUrl}/api/generate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          model: selectedModel,
          prompt: testPrompt,
          stream: false,
          options: {
            temperature,
            num_predict: maxTokens,
          },
        }),
      });

      if (!response.ok) throw new Error('Model test failed');

      const data = await response.json();
      const endTime = Date.now();
      const responseTime = endTime - startTime;

      const result = {
        model: selectedModel,
        prompt: testPrompt,
        response: data.response || '',
        responseTime,
        tokensGenerated: data.eval_count || 0,
        timestamp: new Date().toLocaleTimeString(),
        temperature,
        maxTokens,
      };

      setTestResult(result);
      setTestHistory([result, ...testHistory.slice(0, 9)]);
    } catch (error) {
      logger.error('Error running test:', error);
      setTestError(`Test failed: ${error.message}`);
    } finally {
      setTestLoading(false);
    }
  };

  // ============================================================================
  // RENDER
  // ============================================================================

  return (
    <div className="model-management-container">
      <div className="dashboard-header">
        <h1 className="dashboard-title">🤖 AI Studio</h1>
        <p className="dashboard-subtitle">
          Deploy, test, and train AI models with unified management
        </p>
      </div>

      {/* Tab Navigation */}
      <div className="model-tabs">
        <button
          className={`tab-btn ${activeTab === 'models' ? 'active' : ''}`}
          onClick={() => setActiveTab('models')}
        >
          📊 Models
        </button>
        <button
          className={`tab-btn ${activeTab === 'test' ? 'active' : ''}`}
          onClick={() => setActiveTab('test')}
        >
          🧪 Test Models
        </button>
        <button
          className={`tab-btn ${activeTab === 'training' ? 'active' : ''}`}
          onClick={() => setActiveTab('training')}
        >
          📚 Training Data
        </button>
        <button
          className={`tab-btn ${activeTab === 'history' ? 'active' : ''}`}
          onClick={() => setActiveTab('history')}
        >
          📈 Test History
        </button>
      </div>

      {/* MODELS TAB */}
      {activeTab === 'models' && (
        <>
          {ollamaModels.length > 0 && (
            <div className="ollama-models-section">
              <h2 className="section-title">🚀 Local Ollama Models</h2>
              <div className="models-grid">
                {ollamaModels.map((model) => (
                  <div key={model.name} className="model-card ollama-model">
                    <div className="model-header">
                      <div className="model-info">
                        <h3 className="model-name">{model.name}</h3>
                        <p className="model-provider">Ollama (Local)</p>
                      </div>
                      <span className="status-badge status-active">Local</span>
                    </div>
                    <div className="model-version">
                      <span className="version-label">Size:</span>
                      <span className="version-value">
                        {(model.size / (1024 * 1024 * 1024)).toFixed(1)}GB
                      </span>
                    </div>
                    <div className="model-metrics">
                      <div className="metric">
                        <span className="metric-label">Model</span>
                        <span className="metric-value">{model.model}</span>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="provider-models-section">
            <h2 className="section-title">☁️ Cloud Provider Models</h2>
            {modelsLoading && <p className="loading-text">Loading models...</p>}
            <div className="models-grid">
              {models.map((model) => (
                <div key={model.id} className="model-card">
                  <div className="model-header">
                    <div className="model-info">
                      <h3 className="model-name">{model.name}</h3>
                      <p className="model-provider">{model.provider}</p>
                    </div>
                    <span
                      className={`status-badge status-${model.status.toLowerCase()}`}
                    >
                      {model.status}
                    </span>
                  </div>
                  <div className="model-version">
                    <span className="version-label">Display Name:</span>
                    <span className="version-value">
                      {model.displayName || model.name}
                    </span>
                  </div>
                  <div className="model-metrics">
                    <div className="metric">
                      <span className="metric-label">Size</span>
                      <span className="metric-value">
                        {model.size || 'unknown'}
                      </span>
                    </div>
                    <div className="metric">
                      <span className="metric-label">Free</span>
                      <span className="metric-value">
                        {model.isFree ? 'Yes' : 'No'}
                      </span>
                    </div>
                    <div className="metric">
                      <span className="metric-label">Description</span>
                      <span className="metric-value">
                        {model.description || '-'}
                      </span>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </>
      )}

      {/* TEST MODELS TAB */}
      {activeTab === 'test' && (
        <div className="model-test-section">
          <div className="test-controls">
            <div className="control-group">
              <label>Select Model</label>
              <select
                value={selectedModel}
                onChange={(e) => setSelectedModel(e.target.value)}
              >
                <option value="">Choose a model...</option>
                {ollamaModels.map((model) => (
                  <option key={model.name} value={model.name}>
                    {model.name}
                  </option>
                ))}
              </select>
            </div>

            <div className="control-group">
              <label>Temperature ({temperature})</label>
              <input
                type="range"
                min="0"
                max="1"
                step="0.1"
                value={temperature}
                onChange={(e) => setTemperature(parseFloat(e.target.value))}
              />
            </div>

            <div className="control-group">
              <label>Max Tokens ({maxTokens})</label>
              <input
                type="number"
                min="10"
                max="2000"
                value={maxTokens}
                onChange={(e) => setMaxTokens(parseInt(e.target.value))}
              />
            </div>
          </div>

          <div className="test-prompt">
            <label>Test Prompt</label>
            <textarea
              value={testPrompt}
              onChange={(e) => setTestPrompt(e.target.value)}
              placeholder="Enter your test prompt here..."
            />
            <button
              className="btn btn-primary"
              onClick={runModelTest}
              disabled={testLoading}
            >
              {testLoading ? 'Testing...' : '▶️ Run Test'}
            </button>
          </div>

          {testError && <div className="error-message">{testError}</div>}

          {testResult && (
            <div className="test-result">
              <h3>Test Result</h3>
              <div className="result-meta">
                <span>Model: {testResult.model}</span>
                <span>Response Time: {testResult.responseTime}ms</span>
                <span>Tokens: {testResult.tokensGenerated}</span>
              </div>
              <div className="result-response">
                <p>{testResult.response}</p>
              </div>
            </div>
          )}
        </div>
      )}

      {/* TRAINING DATA TAB */}
      {activeTab === 'training' && (
        <div className="training-section">
          {trainingError && (
            <div className="error-message">{trainingError}</div>
          )}
          {trainingLoading && <p>Loading training data...</p>}

          {stats && (
            <div className="training-stats">
              <h3>Training Statistics</h3>
              <div className="stats-grid">
                <div className="stat-card">
                  <span className="stat-label">Total Tasks</span>
                  <span className="stat-value">{stats.total_tasks || 0}</span>
                </div>
                <div className="stat-card">
                  <span className="stat-label">Success Rate</span>
                  <span className="stat-value">
                    {stats.success_rate
                      ? `${(stats.success_rate * 100).toFixed(1)}%`
                      : 'N/A'}
                  </span>
                </div>
                <div className="stat-card">
                  <span className="stat-label">Avg Quality</span>
                  <span className="stat-value">
                    {stats.avg_quality ? stats.avg_quality.toFixed(2) : 'N/A'}
                  </span>
                </div>
              </div>
            </div>
          )}

          <div className="datasets-section">
            <h3>Datasets ({datasets.length})</h3>
            {datasets.length > 0 ? (
              <div className="datasets-list">
                {datasets.map((dataset, idx) => (
                  <div key={idx} className="dataset-item">
                    <span className="dataset-name">
                      {dataset.name || 'Unnamed'}
                    </span>
                    <span className="dataset-count">
                      {dataset.item_count || 0} items
                    </span>
                  </div>
                ))}
              </div>
            ) : (
              <p>No datasets found</p>
            )}
          </div>

          <div className="training-jobs-section">
            <h3>Training Jobs ({trainingJobs.length})</h3>
            {trainingJobs.length > 0 ? (
              <div className="jobs-list">
                {trainingJobs.map((job, idx) => (
                  <div key={idx} className="job-item">
                    <span className="job-name">
                      {job.name || 'Unnamed Job'}
                    </span>
                    <span className="job-status">
                      {job.status || 'pending'}
                    </span>
                  </div>
                ))}
              </div>
            ) : (
              <p>No training jobs</p>
            )}
          </div>
        </div>
      )}

      {/* TEST HISTORY TAB */}
      {activeTab === 'history' && (
        <div className="test-history-section">
          {testHistory.length > 0 ? (
            <div className="history-list">
              {testHistory.map((result, idx) => (
                <div key={idx} className="history-item">
                  <div className="history-header">
                    <span className="history-model">{result.model}</span>
                    <span className="history-time">{result.timestamp}</span>
                  </div>
                  <div className="history-prompt">
                    <strong>Prompt:</strong> {result.prompt.substring(0, 100)}
                  </div>
                  <div className="history-meta">
                    <span>Response Time: {result.responseTime}ms</span>
                    <span>Tokens: {result.tokensGenerated}</span>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p>No test history yet. Run a model test to see results.</p>
          )}
        </div>
      )}
    </div>
  );
}

export default AIStudio;
