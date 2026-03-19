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
      authInitialized: false,

      setUser: (user) => set({ user }),
      setAccessToken: (token) => set({ accessToken: token }),
      setIsAuthenticated: (isAuth) => set({ isAuthenticated: isAuth }),
      setAuthInitialized: (initialized) =>
        set({ authInitialized: initialized }),

      logout: () =>
        set({
          user: null,
          accessToken: null,
          refreshToken: null,
          isAuthenticated: false,
          authInitialized: true,
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

      setSelectedModel: (model) =>
        set((state) => ({
          orchestrator: {
            ...state.orchestrator,
            selectedModel: model,
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

      // ===== MESSAGE STREAM STATE (Phase 3B) =====
      messages: [], // Unified message stream for CommandPane integration

      /**
       * Add message to stream (command, status, result, or error).
       * Capped at MAX_MESSAGES to prevent unbounded memory growth in long-running sessions.
       * @param {Object} message - Message object with type, content, metadata
       */
      addMessage: (message) =>
        set((state) => {
          const MAX_MESSAGES = 200;
          const updated = [
            ...state.messages,
            {
              ...message,
              timestamp: new Date().toISOString(),
              id: `msg-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
            },
          ];
          // Evict oldest entries when over the cap (keep the most recent MAX_MESSAGES)
          return {
            messages:
              updated.length > MAX_MESSAGES
                ? updated.slice(-MAX_MESSAGES)
                : updated,
          };
        }),

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
      // Bump version to clear stale apiKeys from localStorage on existing clients.
      version: 1,
      migrate: (persisted, version) => {
        if (version === 0 && persisted) {
          // Drop apiKeys that may have been persisted by older versions
          const { apiKeys, ...rest } = persisted;
          return rest;
        }
        return persisted;
      },
      partialize: (state) => ({
        // Persist non-sensitive auth state only (session token is HttpOnly cookie).
        user: state.user,
        isAuthenticated: state.isAuthenticated,

        // Existing: UI preferences
        theme: state.theme,
        autoRefresh: state.autoRefresh,
        notifications: state.notifications,
      }), // persist theme and other settings
    }
  )
);

export default useStore;
