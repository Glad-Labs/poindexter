/**
 * messageTypes.test.js
 *
 * Unit tests for lib/messageTypes.js.
 *
 * Tests cover:
 * - MESSAGE_TYPES — all expected constants present with correct values
 * - MESSAGE_TYPE_DESCRIPTIONS — each type has label/direction/renderer/hasActions
 * - MESSAGE_RENDERERS — maps all types to renderer component names
 * - isOrchestratorMessage — true for orchestrator types, false for user/AI types
 * - isUserMessage — true for USER_MESSAGE and ORCHESTRATOR_COMMAND, false for others
 * - hasActions — true for types with actions defined, false otherwise
 * - getAvailableActions — returns correct action arrays, empty for unknown type
 * - isStatusUpdate — true only for ORCHESTRATOR_STATUS
 * - isResult — true only for ORCHESTRATOR_RESULT
 * - isError — true only for ORCHESTRATOR_ERROR
 * - MessageRouter — returns null for invalid message, null for unknown type, routing object otherwise
 *
 * logger is mocked to prevent console noise.
 */

import { vi } from 'vitest';

vi.mock('@/lib/logger', () => ({
  default: { warn: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

import {
  MESSAGE_TYPES,
  MESSAGE_TYPE_DESCRIPTIONS,
  MESSAGE_RENDERERS,
  MessageRouter,
  isOrchestratorMessage,
  isUserMessage,
  hasActions,
  getAvailableActions,
  isStatusUpdate,
  isResult,
  isError,
} from '../messageTypes';

// ---------------------------------------------------------------------------
// MESSAGE_TYPES constants
// ---------------------------------------------------------------------------

describe('MESSAGE_TYPES', () => {
  it('has USER_MESSAGE constant', () => {
    expect(MESSAGE_TYPES.USER_MESSAGE).toBe('user_message');
  });

  it('has AI_MESSAGE constant', () => {
    expect(MESSAGE_TYPES.AI_MESSAGE).toBe('ai_message');
  });

  it('has all four orchestrator types', () => {
    expect(MESSAGE_TYPES.ORCHESTRATOR_COMMAND).toBeDefined();
    expect(MESSAGE_TYPES.ORCHESTRATOR_STATUS).toBeDefined();
    expect(MESSAGE_TYPES.ORCHESTRATOR_RESULT).toBeDefined();
    expect(MESSAGE_TYPES.ORCHESTRATOR_ERROR).toBeDefined();
  });

  it('has exactly 6 message types', () => {
    expect(Object.keys(MESSAGE_TYPES)).toHaveLength(6);
  });
});

// ---------------------------------------------------------------------------
// MESSAGE_TYPE_DESCRIPTIONS
// ---------------------------------------------------------------------------

describe('MESSAGE_TYPE_DESCRIPTIONS', () => {
  it('has a description for every MESSAGE_TYPE', () => {
    for (const type of Object.values(MESSAGE_TYPES)) {
      expect(
        MESSAGE_TYPE_DESCRIPTIONS,
        `Missing description for ${type}`
      ).toHaveProperty(type);
    }
  });

  it('each description has required fields', () => {
    for (const [type, desc] of Object.entries(MESSAGE_TYPE_DESCRIPTIONS)) {
      expect(desc, `${type} missing label`).toHaveProperty('label');
      expect(desc, `${type} missing direction`).toHaveProperty('direction');
      expect(desc, `${type} missing renderer`).toHaveProperty('renderer');
      expect(desc, `${type} missing hasActions`).toHaveProperty('hasActions');
    }
  });

  it('USER_MESSAGE is outgoing and has no actions', () => {
    const desc = MESSAGE_TYPE_DESCRIPTIONS[MESSAGE_TYPES.USER_MESSAGE];
    expect(desc.direction).toBe('outgoing');
    expect(desc.hasActions).toBe(false);
  });

  it('ORCHESTRATOR_RESULT is incoming and has actions', () => {
    const desc = MESSAGE_TYPE_DESCRIPTIONS[MESSAGE_TYPES.ORCHESTRATOR_RESULT];
    expect(desc.direction).toBe('incoming');
    expect(desc.hasActions).toBe(true);
    expect(desc.actions).toContain('approve');
  });
});

// ---------------------------------------------------------------------------
// MESSAGE_RENDERERS
// ---------------------------------------------------------------------------

describe('MESSAGE_RENDERERS', () => {
  it('maps every MESSAGE_TYPE to a renderer string', () => {
    for (const type of Object.values(MESSAGE_TYPES)) {
      expect(
        MESSAGE_RENDERERS[type],
        `Missing renderer for ${type}`
      ).toBeDefined();
      expect(typeof MESSAGE_RENDERERS[type]).toBe('string');
    }
  });

  it('user_message maps to DefaultUserMessage', () => {
    expect(MESSAGE_RENDERERS[MESSAGE_TYPES.USER_MESSAGE]).toBe(
      'DefaultUserMessage'
    );
  });

  it('orchestrator_result maps to OrchestratorResultMessage', () => {
    expect(MESSAGE_RENDERERS[MESSAGE_TYPES.ORCHESTRATOR_RESULT]).toBe(
      'OrchestratorResultMessage'
    );
  });
});

// ---------------------------------------------------------------------------
// isOrchestratorMessage
// ---------------------------------------------------------------------------

describe('isOrchestratorMessage', () => {
  it('returns true for ORCHESTRATOR_COMMAND', () => {
    expect(isOrchestratorMessage(MESSAGE_TYPES.ORCHESTRATOR_COMMAND)).toBe(
      true
    );
  });

  it('returns true for ORCHESTRATOR_STATUS', () => {
    expect(isOrchestratorMessage(MESSAGE_TYPES.ORCHESTRATOR_STATUS)).toBe(true);
  });

  it('returns true for ORCHESTRATOR_RESULT', () => {
    expect(isOrchestratorMessage(MESSAGE_TYPES.ORCHESTRATOR_RESULT)).toBe(true);
  });

  it('returns true for ORCHESTRATOR_ERROR', () => {
    expect(isOrchestratorMessage(MESSAGE_TYPES.ORCHESTRATOR_ERROR)).toBe(true);
  });

  it('returns false for USER_MESSAGE', () => {
    expect(isOrchestratorMessage(MESSAGE_TYPES.USER_MESSAGE)).toBe(false);
  });

  it('returns false for AI_MESSAGE', () => {
    expect(isOrchestratorMessage(MESSAGE_TYPES.AI_MESSAGE)).toBe(false);
  });

  it('returns false for unknown type', () => {
    expect(isOrchestratorMessage('unknown_type')).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// isUserMessage
// ---------------------------------------------------------------------------

describe('isUserMessage', () => {
  it('returns true for USER_MESSAGE', () => {
    expect(isUserMessage(MESSAGE_TYPES.USER_MESSAGE)).toBe(true);
  });

  it('returns true for ORCHESTRATOR_COMMAND', () => {
    expect(isUserMessage(MESSAGE_TYPES.ORCHESTRATOR_COMMAND)).toBe(true);
  });

  it('returns false for AI_MESSAGE', () => {
    expect(isUserMessage(MESSAGE_TYPES.AI_MESSAGE)).toBe(false);
  });

  it('returns false for ORCHESTRATOR_STATUS', () => {
    expect(isUserMessage(MESSAGE_TYPES.ORCHESTRATOR_STATUS)).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// hasActions
// ---------------------------------------------------------------------------

describe('hasActions', () => {
  it('returns false for USER_MESSAGE', () => {
    expect(hasActions(MESSAGE_TYPES.USER_MESSAGE)).toBe(false);
  });

  it('returns false for AI_MESSAGE', () => {
    expect(hasActions(MESSAGE_TYPES.AI_MESSAGE)).toBe(false);
  });

  it('returns true for ORCHESTRATOR_COMMAND', () => {
    expect(hasActions(MESSAGE_TYPES.ORCHESTRATOR_COMMAND)).toBe(true);
  });

  it('returns true for ORCHESTRATOR_RESULT', () => {
    expect(hasActions(MESSAGE_TYPES.ORCHESTRATOR_RESULT)).toBe(true);
  });

  it('returns false for unknown type', () => {
    expect(hasActions('nonexistent')).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// getAvailableActions
// ---------------------------------------------------------------------------

describe('getAvailableActions', () => {
  it('returns empty array for USER_MESSAGE', () => {
    expect(getAvailableActions(MESSAGE_TYPES.USER_MESSAGE)).toEqual([]);
  });

  it('returns actions array for ORCHESTRATOR_COMMAND', () => {
    const actions = getAvailableActions(MESSAGE_TYPES.ORCHESTRATOR_COMMAND);
    expect(actions).toContain('execute');
    expect(actions).toContain('cancel');
  });

  it('returns actions array for ORCHESTRATOR_RESULT', () => {
    const actions = getAvailableActions(MESSAGE_TYPES.ORCHESTRATOR_RESULT);
    expect(actions).toContain('approve');
    expect(actions).toContain('reject');
  });

  it('returns empty array for unknown type', () => {
    expect(getAvailableActions('unknown')).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// isStatusUpdate
// ---------------------------------------------------------------------------

describe('isStatusUpdate', () => {
  it('returns true for ORCHESTRATOR_STATUS', () => {
    expect(isStatusUpdate(MESSAGE_TYPES.ORCHESTRATOR_STATUS)).toBe(true);
  });

  it('returns false for all other types', () => {
    for (const type of Object.values(MESSAGE_TYPES)) {
      if (type !== MESSAGE_TYPES.ORCHESTRATOR_STATUS) {
        expect(isStatusUpdate(type)).toBe(false);
      }
    }
  });
});

// ---------------------------------------------------------------------------
// isResult
// ---------------------------------------------------------------------------

describe('isResult', () => {
  it('returns true for ORCHESTRATOR_RESULT', () => {
    expect(isResult(MESSAGE_TYPES.ORCHESTRATOR_RESULT)).toBe(true);
  });

  it('returns false for other types', () => {
    expect(isResult(MESSAGE_TYPES.ORCHESTRATOR_STATUS)).toBe(false);
    expect(isResult(MESSAGE_TYPES.USER_MESSAGE)).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// isError
// ---------------------------------------------------------------------------

describe('isError', () => {
  it('returns true for ORCHESTRATOR_ERROR', () => {
    expect(isError(MESSAGE_TYPES.ORCHESTRATOR_ERROR)).toBe(true);
  });

  it('returns false for other types', () => {
    expect(isError(MESSAGE_TYPES.ORCHESTRATOR_RESULT)).toBe(false);
    expect(isError(MESSAGE_TYPES.AI_MESSAGE)).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// MessageRouter
// ---------------------------------------------------------------------------

describe('MessageRouter', () => {
  it('returns null when message is null', () => {
    expect(MessageRouter({ message: null, onAction: vi.fn() })).toBeNull();
  });

  it('returns null when message has no type', () => {
    expect(MessageRouter({ message: {}, onAction: vi.fn() })).toBeNull();
  });

  it('returns null when message type is unknown', () => {
    expect(
      MessageRouter({
        message: { type: 'nonexistent_type' },
        onAction: vi.fn(),
      })
    ).toBeNull();
  });

  it('returns routing object for valid message type', () => {
    const onAction = vi.fn();
    const message = { type: MESSAGE_TYPES.USER_MESSAGE, text: 'Hello' };
    const result = MessageRouter({ message, onAction });
    expect(result).not.toBeNull();
    expect(result.rendererName).toBe('DefaultUserMessage');
    expect(result.message).toBe(message);
    expect(result.onAction).toBe(onAction);
  });

  it('routes ORCHESTRATOR_RESULT to correct renderer', () => {
    const result = MessageRouter({
      message: { type: MESSAGE_TYPES.ORCHESTRATOR_RESULT, content: 'Article' },
      onAction: vi.fn(),
    });
    expect(result.rendererName).toBe('OrchestratorResultMessage');
  });
});
