import { create } from 'zustand';
import { persist } from 'zustand/middleware';

const useStore = create(
  persist(
    (set) => ({
      // ===== AUTHENTICATION STATE =====
      user: null,
      accessToken: null,
      refreshToken: null,
      isAuthenticated: false,

      setUser: (user) => set({ user }),
      setAccessToken: (token) => set({ accessToken: token }),
      setRefreshToken: (token) => set({ refreshToken: token }),
      setIsAuthenticated: (isAuth) => set({ isAuthenticated: isAuth }),

      logout: () =>
        set({
          user: null,
          accessToken: null,
          refreshToken: null,
          isAuthenticated: false,
          tasks: [],
          selectedTask: null,
        }),

      // ===== TASK STATE =====
      tasks: [],
      selectedTask: null,
      isModalOpen: false,

      setTasks: (tasks) => set({ tasks }),
      setSelectedTask: (task) =>
        set({ selectedTask: task, isModalOpen: !!task }),
      setIsModalOpen: (isOpen) => set({ isModalOpen: isOpen }),

      // ===== TASK ACTION STATE (Phase 1.2) =====
      taskActionLoading: {}, // { taskId: boolean }
      taskActionError: {}, // { taskId: string|null }

      setTaskActionLoading: (taskId, loading) =>
        set((state) => ({
          taskActionLoading: {
            ...state.taskActionLoading,
            [taskId]: loading,
          },
        })),

      setTaskActionError: (taskId, error) =>
        set((state) => ({
          taskActionError: {
            ...state.taskActionError,
            [taskId]: error,
          },
        })),

      clearTaskAction: (taskId) =>
        set((state) => ({
          taskActionLoading: {
            ...state.taskActionLoading,
            [taskId]: false,
          },
          taskActionError: {
            ...state.taskActionError,
            [taskId]: null,
          },
        })),

      // ===== METRICS STATE =====
      metrics: {
        totalTasks: 0,
        completedTasks: 0,
        failedTasks: 0,
        successRate: 0,
        avgExecutionTime: 0,
        totalCost: 0,
      },
      setMetrics: (metrics) => set({ metrics }),

      // ===== UI STATE =====
      theme: 'dark', // default to dark theme
      autoRefresh: false,
      notifications: {
        desktop: true,
      },
      apiKeys: {
        mercury: '',
        gcp: '',
      },

      setTheme: (theme) => set({ theme }),
      toggleTheme: () =>
        set((state) => ({ theme: state.theme === 'light' ? 'dark' : 'light' })),
      toggleAutoRefresh: () =>
        set((state) => ({ autoRefresh: !state.autoRefresh })),
      toggleDesktopNotifications: () =>
        set((state) => ({
          notifications: {
            ...state.notifications,
            desktop: !state.notifications.desktop,
          },
        })),
      setApiKey: (key, value) =>
        set((state) => ({
          apiKeys: {
            ...state.apiKeys,
            [key]: value,
          },
        })),

      // ===== ORCHESTRATOR STATE (NEW) =====
      orchestrator: {
        mode: 'conversation', // 'agent' or 'conversation'
        activeHost: 'ollama', // 'github', 'azure', 'openai', 'anthropic', 'google', 'ollama'
        selectedModel: 'gpt-4', // Model selection for current host
        hostConfigs: {
          github: { enabled: false, apiKey: '' },
          azure: { enabled: false, endpoint: '', apiKey: '' },
          openai: { enabled: false, apiKey: '' },
          anthropic: { enabled: false, apiKey: '' },
          google: { enabled: false, apiKey: '' },
          ollama: { enabled: true, endpoint: 'http://localhost:11434' },
        },
        currentExecution: {
          executionId: null,
          status: 'idle', // 'idle', 'pending', 'executing', 'completed', 'failed'
          commandType: null,
          startedAt: null,
          completedAt: null,
          phases: [],
          currentPhaseIndex: 0,
          progress: 0,
          error: null,
        },
        executionHistory: [],
      },

      setOrchestratorMode: (mode) =>
        set((state) => ({
          orchestrator: {
            ...state.orchestrator,
            mode: mode === 'agent' ? 'agent' : 'conversation',
          },
        })),

      setActiveHost: (host) =>
        set((state) => ({
          orchestrator: {
            ...state.orchestrator,
            activeHost: host,
          },
        })),

      setSelectedModel: (model) =>
        set((state) => ({
          orchestrator: {
            ...state.orchestrator,
            selectedModel: model,
          },
        })),

      updateHostConfig: (host, config) =>
        set((state) => ({
          orchestrator: {
            ...state.orchestrator,
            hostConfigs: {
              ...state.orchestrator.hostConfigs,
              [host]: { ...state.orchestrator.hostConfigs[host], ...config },
            },
          },
        })),

      startExecution: (executionId, commandType, phases = []) =>
        set((state) => ({
          orchestrator: {
            ...state.orchestrator,
            currentExecution: {
              executionId,
              status: 'pending',
              commandType,
              startedAt: new Date().toISOString(),
              completedAt: null,
              phases,
              currentPhaseIndex: 0,
              progress: 0,
              error: null,
            },
          },
        })),

      updateExecutionPhase: (phaseIndex, phaseData) =>
        set((state) => {
          const newExecution = { ...state.orchestrator.currentExecution };
          if (phaseIndex >= 0 && phaseIndex < newExecution.phases.length) {
            newExecution.phases[phaseIndex] = {
              ...newExecution.phases[phaseIndex],
              ...phaseData,
            };
            newExecution.currentPhaseIndex = phaseIndex;
            newExecution.progress = Math.round(
              ((phaseIndex + 1) / newExecution.phases.length) * 100
            );
            newExecution.status = 'executing';
          }
          return {
            orchestrator: {
              ...state.orchestrator,
              currentExecution: newExecution,
            },
          };
        }),

      completeExecution: (_result) =>
        set((state) => {
          const completedExecution = {
            ...state.orchestrator.currentExecution,
            status: 'completed',
            completedAt: new Date().toISOString(),
            progress: 100,
          };
          return {
            orchestrator: {
              ...state.orchestrator,
              currentExecution: completedExecution,
              executionHistory: [
                completedExecution,
                ...state.orchestrator.executionHistory,
              ].slice(0, 50), // Keep last 50
            },
          };
        }),

      failExecution: (error) =>
        set((state) => ({
          orchestrator: {
            ...state.orchestrator,
            currentExecution: {
              ...state.orchestrator.currentExecution,
              status: 'failed',
              completedAt: new Date().toISOString(),
              error,
            },
          },
        })),

      resetExecution: () =>
        set((state) => ({
          orchestrator: {
            ...state.orchestrator,
            currentExecution: {
              executionId: null,
              status: 'idle',
              commandType: null,
              startedAt: null,
              completedAt: null,
              phases: [],
              currentPhaseIndex: 0,
              progress: 0,
              error: null,
            },
          },
        })),

      clearExecutionHistory: () =>
        set((state) => ({
          orchestrator: {
            ...state.orchestrator,
            executionHistory: [],
          },
        })),

      // ===== MESSAGE STREAM STATE (Phase 3B) =====
      messages: [], // Unified message stream for CommandPane integration

      /**
       * Add message to stream (command, status, result, or error)
       * @param {Object} message - Message object with type, content, metadata
       */
      addMessage: (message) =>
        set((state) => ({
          messages: [
            ...state.messages,
            {
              ...message,
              timestamp: new Date().toISOString(),
              id: `msg-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
            },
          ],
        })),

      /**
       * Update existing message in stream (e.g., update progress)
       * @param {number} index - Index of message to update
       * @param {Object} updates - Fields to update
       */
      updateMessage: (index, updates) =>
        set((state) => {
          const newMessages = [...state.messages];
          if (index >= 0 && index < newMessages.length) {
            newMessages[index] = { ...newMessages[index], ...updates };
          }
          return { messages: newMessages };
        }),

      /**
       * Update message by ID (alternative to index-based update)
       * @param {string} messageId - Message ID to update
       * @param {Object} updates - Fields to update
       */
      updateMessageById: (messageId, updates) =>
        set((state) => ({
          messages: state.messages.map((msg) =>
            msg.id === messageId ? { ...msg, ...updates } : msg
          ),
        })),

      /**
       * Clear all messages from stream
       */
      clearMessages: () => set({ messages: [] }),

      /**
       * Remove specific message from stream
       * @param {number} index - Index of message to remove
       */
      removeMessage: (index) =>
        set((state) => {
          const newMessages = [...state.messages];
          if (index >= 0 && index < newMessages.length) {
            newMessages.splice(index, 1);
          }
          return { messages: newMessages };
        }),
    }),
    {
      name: 'oversight-hub-storage',
      partialize: (state) => ({
        // Persist non-sensitive auth state only (session token is HttpOnly cookie).
        user: state.user,
        isAuthenticated: state.isAuthenticated,

        // Existing: UI preferences
        theme: state.theme,
        autoRefresh: state.autoRefresh,
        notifications: state.notifications,
        apiKeys: state.apiKeys,
      }), // persist theme and other settings
    }
  )
);

export default useStore;
