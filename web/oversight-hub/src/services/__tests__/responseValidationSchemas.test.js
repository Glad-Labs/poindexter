import {
  validateCostMetrics,
  validateCostsByPhase,
  validateCostsByModel,
  validateCostHistory,
  validateBudgetStatus,
  validateTask,
  validateTaskList,
  validateSettings,
  validateGeneratedImage,
  safeValidate,
} from '../responseValidationSchemas';

describe('responseValidationSchemas', () => {
  describe('validateCostMetrics', () => {
    test('validates correct cost metrics (flat shape)', () => {
      const validMetrics = {
        total_cost: 127.5,
        avg_cost_per_task: 0.0087,
        total_tasks: 15000,
      };

      const result = validateCostMetrics(validMetrics);
      expect(result).toEqual(validMetrics);
    });

    test('validates cost metrics with nested tasks shape (backend format)', () => {
      const backendResponse = {
        total_cost: 12.5,
        tasks: {
          completed: 42,
          avg_cost_per_task: 0.3,
        },
      };

      const result = validateCostMetrics(backendResponse);
      expect(result).toEqual({
        total_cost: 12.5,
        avg_cost_per_task: 0.3,
        total_tasks: 42,
      });
    });

    test('defaults missing avg_cost_per_task and total_tasks to 0', () => {
      const minimalResponse = { total_cost: 0 };
      const result = validateCostMetrics(minimalResponse);
      expect(result).toEqual({
        total_cost: 0,
        avg_cost_per_task: 0,
        total_tasks: 0,
      });
    });

    test('throws on negative total_cost', () => {
      const invalidMetrics = {
        total_cost: -10,
        avg_cost_per_task: 0.01,
        total_tasks: 100,
      };

      expect(() => validateCostMetrics(invalidMetrics)).toThrow(
        'total_cost must be a non-negative number'
      );
    });

    test('throws on non-numeric avg_cost_per_task', () => {
      const invalidMetrics = {
        total_cost: 127.5,
        avg_cost_per_task: 'invalid',
        total_tasks: 100,
      };

      expect(() => validateCostMetrics(invalidMetrics)).toThrow();
    });

    test('throws on null input', () => {
      expect(() => validateCostMetrics(null)).toThrow(
        'Cost metrics must be an object'
      );
    });
  });

  describe('validateCostsByPhase', () => {
    test('validates correct phase breakdown (legacy object format)', () => {
      const validPhaseData = {
        phases: {
          research: 45.5,
          generation: 52.3,
          review: 29.7,
        },
      };

      const result = validateCostsByPhase(validPhaseData);
      expect(result.phases).toEqual(validPhaseData.phases);
    });

    test('validates phase breakdown as array (backend format)', () => {
      const backendPhaseData = {
        phases: [
          { phase: 'research', total_cost: 0.5, task_count: 5 },
          { phase: 'draft', total_cost: 2.0, task_count: 10 },
        ],
      };

      const result = validateCostsByPhase(backendPhaseData);
      expect(result.phases).toHaveLength(2);
    });

    test('throws on negative phase cost (legacy format)', () => {
      const invalidPhaseData = {
        phases: {
          research: -10,
          generation: 50,
        },
      };

      expect(() => validateCostsByPhase(invalidPhaseData)).toThrow();
    });

    test('allows empty phases array', () => {
      const validPhaseData = { phases: [] };
      const result = validateCostsByPhase(validPhaseData);
      expect(result.phases).toEqual([]);
    });

    test('allows empty phases object', () => {
      const validPhaseData = { phases: {} };
      const result = validateCostsByPhase(validPhaseData);
      expect(result.phases).toEqual({});
    });
  });

  describe('validateCostsByModel', () => {
    test('validates correct model breakdown (legacy object format)', () => {
      const validModelData = {
        models: {
          'claude-3.5-sonnet': 45.5,
          'gpt-4-turbo': 52.3,
          'gemini-pro': 29.7,
        },
      };

      const result = validateCostsByModel(validModelData);
      expect(result.models).toEqual(validModelData.models);
    });

    test('validates model breakdown as array (backend format)', () => {
      const backendModelData = {
        models: [
          {
            model: 'gpt-4',
            total_cost: 2.0,
            task_count: 10,
            provider: 'openai',
          },
          {
            model: 'ollama',
            total_cost: 0.0,
            task_count: 5,
            provider: 'ollama',
          },
        ],
      };

      const result = validateCostsByModel(backendModelData);
      expect(result.models).toHaveLength(2);
    });

    test('throws on negative model cost (legacy format)', () => {
      const invalidModelData = {
        models: {
          'claude-3.5-sonnet': -10,
          'gpt-4-turbo': 50,
        },
      };

      expect(() => validateCostsByModel(invalidModelData)).toThrow();
    });

    test('allows empty models array', () => {
      const validModelData = { models: [] };
      const result = validateCostsByModel(validModelData);
      expect(result.models).toEqual([]);
    });

    test('allows empty models object', () => {
      const validModelData = { models: {} };
      const result = validateCostsByModel(validModelData);
      expect(result.models).toEqual({});
    });
  });

  describe('validateCostHistory', () => {
    test('validates correct cost history', () => {
      const validHistory = {
        daily_data: [
          { date: '2026-02-10', cost: 5.5 },
          { date: '2026-02-09', cost: 4.3 },
        ],
      };

      const result = validateCostHistory(validHistory);
      expect(result.daily_data).toHaveLength(2);
    });

    test('throws on missing date field', () => {
      const invalidHistory = {
        daily_data: [{ cost: 5.5 }],
      };

      expect(() => validateCostHistory(invalidHistory)).toThrow();
    });

    test('throws on non-numeric cost', () => {
      const invalidHistory = {
        daily_data: [{ date: '2026-02-10', cost: 'invalid' }],
      };

      expect(() => validateCostHistory(invalidHistory)).toThrow();
    });

    test('allows empty history', () => {
      const validHistory = { daily_data: [] };
      const result = validateCostHistory(validHistory);
      expect(result.daily_data).toEqual([]);
    });
  });

  describe('validateBudgetStatus', () => {
    test('validates correct budget status', () => {
      const validBudget = {
        monthly_budget: 150.0,
        amount_spent: 127.5,
        amount_remaining: 22.5,
        percent_used: 85,
      };

      const result = validateBudgetStatus(validBudget);
      expect(result).toEqual(validBudget);
    });

    test('allows percent_used > 100 (budget exceeded)', () => {
      const overBudget = {
        monthly_budget: 150.0,
        amount_spent: 225,
        amount_remaining: -75,
        percent_used: 150,
      };

      const result = validateBudgetStatus(overBudget);
      expect(result.percent_used).toBe(150);
    });

    test('throws on negative amount_spent', () => {
      const invalidBudget = {
        monthly_budget: 150.0,
        amount_spent: -50,
        amount_remaining: 200,
        percent_used: 0,
      };

      expect(() => validateBudgetStatus(invalidBudget)).toThrow();
    });
  });

  describe('validateTask', () => {
    test('validates correct task', () => {
      const validTask = {
        id: 'task-123',
        topic: 'AI and the future',
        status: 'completed',
        content: 'Sample content',
      };

      const result = validateTask(validTask);
      expect(result).toEqual(validTask);
    });

    test('throws on missing id', () => {
      const invalidTask = {
        topic: 'AI and the future',
        status: 'completed',
      };

      expect(() => validateTask(invalidTask)).toThrow('Task must have an id');
    });

    test('throws on empty topic', () => {
      const invalidTask = {
        id: 'task-123',
        topic: '   ',
        status: 'completed',
      };

      expect(() => validateTask(invalidTask)).toThrow();
    });

    test('throws on invalid status', () => {
      const invalidTask = {
        id: 'task-123',
        topic: 'AI and the future',
        status: 'invalid_status',
      };

      expect(() => validateTask(invalidTask)).toThrow(
        'Status must be one of: pending, in_progress, completed, failed'
      );
    });
  });

  describe('validateTaskList', () => {
    test('validates correct task list', () => {
      const validTaskList = {
        tasks: [
          {
            id: 'task-1',
            topic: 'Topic 1',
            status: 'completed',
          },
          {
            id: 'task-2',
            topic: 'Topic 2',
            status: 'pending',
          },
        ],
      };

      const result = validateTaskList(validTaskList);
      expect(result.tasks).toHaveLength(2);
    });

    test('throws on non-array tasks', () => {
      const invalidTaskList = {
        tasks: 'not an array',
      };

      expect(() => validateTaskList(invalidTaskList)).toThrow(
        'tasks must be an array'
      );
    });

    test('throws on invalid task in list', () => {
      const invalidTaskList = {
        tasks: [
          {
            id: 'task-1',
            topic: 'Valid task',
            status: 'completed',
          },
          {
            id: 'task-2',
            topic: '',
            status: 'completed',
          },
        ],
      };

      expect(() => validateTaskList(invalidTaskList)).toThrow('tasks[1]:');
    });
  });

  describe('validateGeneratedImage', () => {
    test('validates correct image response', () => {
      const validImage = {
        image_url: 'https://example.com/image.jpg',
        alt_text: 'Sample image',
      };

      const result = validateGeneratedImage(validImage);
      expect(result.image_url).toBe('https://example.com/image.jpg');
    });

    test('throws on missing image_url', () => {
      const invalidImage = {
        alt_text: 'Sample image',
      };

      expect(() => validateGeneratedImage(invalidImage)).toThrow(
        'image_url must be a non-empty string'
      );
    });

    test('throws on empty image_url', () => {
      const invalidImage = {
        image_url: '',
      };

      expect(() => validateGeneratedImage(invalidImage)).toThrow();
    });
  });

  describe('validateSettings', () => {
    test('validates correct settings', () => {
      const validSettings = {
        theme: 'dark',
        auto_refresh: true,
        mercury_api_key: 'key123',
      };

      const result = validateSettings(validSettings);
      expect(result).toEqual(validSettings);
    });

    test('allows unknown settings keys (for extensibility)', () => {
      const settingsWithUnknownKey = {
        theme: 'dark',
        future_setting: 'some_value',
      };

      const result = validateSettings(settingsWithUnknownKey);
      expect(result).toBeDefined();
    });

    test('allows empty settings object', () => {
      const emptySettings = {};
      const result = validateSettings(emptySettings);
      expect(result).toEqual({});
    });
  });

  describe('safeValidate', () => {
    test('returns validated data on success', () => {
      const validData = {
        total_cost: 100,
        avg_cost_per_task: 0.01,
        total_tasks: 10000,
      };

      const result = safeValidate(validateCostMetrics, validData, 'Test');
      expect(result).toEqual(validData);
    });

    test('returns null on validation error', () => {
      const invalidData = {
        total_cost: 'invalid',
        avg_cost_per_task: -5,
        total_tasks: -100,
      };

      const result = safeValidate(validateCostMetrics, invalidData, 'Test');
      expect(result).toBeNull();
    });

    test('does not throw on invalid data', () => {
      const invalidData = null;

      expect(() => {
        safeValidate(validateCostMetrics, invalidData, 'Test');
      }).not.toThrow();
    });
  });
});
