# Chat Interface Implementation Specification

**Document:** Feature Implementation Guide  
**Feature:** Chat & Conversation Interface  
**Priority:** HIGH (First feature to implement)  
**Effort Estimate:** 6 hours  
**Created:** December 8, 2025

---

## Overview

The FastAPI backend has a complete Chat service (`/api/chat/*`) with multi-model support and conversation history. This document outlines the exact components, hooks, and integration points needed to expose this in the Oversight Hub UI.

---

## Backend API Reference

### Endpoints Ready to Consume

#### 1. POST `/api/chat` - Send Message

```
Request:
{
  "message": "string",
  "model": "ollama|openai|claude|gemini",
  "conversationId": "string (optional)",
  "temperature": 0.0-2.0,
  "max_tokens": 1-4000
}

Response:
{
  "response": "string",
  "model": "string",
  "conversationId": "string",
  "timestamp": "ISO-8601",
  "tokens_used": 123
}
```

#### 2. GET `/api/chat/history/{conversation_id}`

```
Response:
[
  {
    "role": "user|assistant",
    "content": "string",
    "timestamp": "ISO-8601",
    "model": "string (assistant only)"
  }
]
```

#### 3. DELETE `/api/chat/history/{conversation_id}`

```
Response:
{
  "success": true,
  "conversation_id": "string"
}
```

#### 4. GET `/api/chat/models`

```
Response:
[
  {
    "id": "ollama-mistral",
    "name": "Mistral (Ollama)",
    "provider": "ollama",
    "status": "available|unavailable",
    "latency_ms": 150
  }
]
```

---

## Component Architecture

### Component Tree

```
OversightHub.jsx (main)
├── Navigation (add "Chat" button)
└── ChatContainer.jsx (NEW)
    ├── ChatSidebar.jsx (NEW)
    │   ├── ConversationList.jsx (NEW)
    │   └── NewConversationButton.jsx (NEW)
    ├── ChatMain.jsx (NEW)
    │   ├── ConversationHeader.jsx (NEW)
    │   ├── ChatMessages.jsx (NEW)
    │   │   └── ChatMessage.jsx (NEW) × N
    │   ├── ChatInput.jsx (NEW)
    │   └── ModelSelector.jsx (NEW)
    └── ChatSettings.jsx (NEW)
```

---

## File Structure to Create

```javascript
web/oversight-hub/src/
├── components/
│   ├── chat/                                 # NEW folder
│   │   ├── ChatContainer.jsx                 # Main container
│   │   ├── ChatSidebar.jsx                   # Conversation list
│   │   ├── ConversationList.jsx              # Sidebar conversations
│   │   ├── ChatMain.jsx                      # Chat area
│   │   ├── ConversationHeader.jsx            # Title + options
│   │   ├── ChatMessages.jsx                  # Messages wrapper
│   │   ├── ChatMessage.jsx                   # Individual message
│   │   ├── ChatInput.jsx                     # Input form
│   │   ├── ModelSelector.jsx                 # Model dropdown
│   │   ├── ChatSettings.jsx                  # Settings modal
│   │   └── chat.css                          # Styles
│   │
├── hooks/
│   ├── useChat.js                            # Chat logic (NEW)
│   └── useConversation.js                    # Conversation state (NEW)
│
├── services/
│   └── chatService.js                        # API calls (NEW)
│
└── store/
    └── useStore.js                           # Update with chat state
```

---

## Component Specifications

### 1. ChatContainer.jsx (Main Component)

**Purpose:** Top-level chat interface container  
**Props:** None (uses Zustand store)  
**State:** Managed by Zustand + useChat hook

```javascript
// Key features:
- Load conversation list on mount
- Manage current conversation
- Handle model selection
- Render sidebar + main chat area
- Responsive layout (sidebar collapse on mobile)
```

**UI Layout:**

```
┌─────────────┬─────────────────────────────┐
│ Conversation│  Conversation Name         │
│ List        │  [Model: Mistral] [✓ New]  │
│             │─────────────────────────────│
│ [New Chat]  │ ┌─────────────────────────┐ │
│ Yesterday   │ │ Assistant: Hello...     │ │
│ • Chat 1    │ │ User: Hi there!         │ │
│ • Chat 2    │ │ Assistant: How can...   │ │
│             │ └─────────────────────────┘ │
│ Last Week   │ [Input...]                  │
│ • Chat 3    │ [Send] [Model: Mistral ▼]  │
│             │                             │
└─────────────┴─────────────────────────────┘
```

---

### 2. ChatSidebar.jsx

**Purpose:** Left sidebar with conversation list  
**Props:** None  
**State:** `conversations`, `selectedConversationId`, `isLoading`

```javascript
export default function ChatSidebar() {
  const {
    conversations,
    selectedConversationId,
    setSelectedConversation,
    createNewConversation,
    deleteConversation,
    isLoading,
  } = useChat();

  return (
    <div className="chat-sidebar">
      {/* New Chat Button */}
      <button onClick={createNewConversation} className="new-chat-btn">
        ➕ New Chat
      </button>

      {/* Grouped Conversation List */}
      {isLoading ? (
        <div>Loading conversations...</div>
      ) : (
        <ConversationList
          conversations={conversations}
          selected={selectedConversationId}
          onSelect={setSelectedConversation}
          onDelete={deleteConversation}
        />
      )}
    </div>
  );
}
```

---

### 3. ConversationList.jsx

**Purpose:** Display grouped and searchable conversation list  
**Props:** `conversations`, `selected`, `onSelect`, `onDelete`

```javascript
// Features:
- Group conversations by date (Today, Yesterday, Last Week, etc.)
- Search/filter conversations
- Delete with confirmation
- Show last message preview
- Indicate unsaved changes
```

---

### 4. ChatMain.jsx

**Purpose:** Main chat area with messages and input  
**Props:** None  
**State:** `currentConversation`, `messages`, `isLoading`

```javascript
export default function ChatMain() {
  const {
    currentConversation,
    messages,
    isLoading,
    selectedModel,
    setSelectedModel,
  } = useChat();

  if (!currentConversation) {
    return <div className="chat-empty">Select or create a conversation</div>;
  }

  return (
    <div className="chat-main">
      <ConversationHeader conversation={currentConversation} />

      <ChatMessages messages={messages} isLoading={isLoading} />

      <div className="chat-footer">
        <ChatInput />
        <div className="chat-controls">
          <ModelSelector
            selectedModel={selectedModel}
            onModelChange={setSelectedModel}
          />
        </div>
      </div>
    </div>
  );
}
```

---

### 5. ChatMessages.jsx

**Purpose:** Display chat message thread  
**Props:** `messages`, `isLoading`

```javascript
// Features:
- Auto-scroll to latest message
- Show typing indicator when AI responding
- Different styling for user vs assistant
- Copy message button
- Time stamps (HH:MM)
- Markdown rendering for AI responses
```

---

### 6. ChatMessage.jsx

**Purpose:** Individual message component  
**Props:** `message`, `isUserMessage`

```javascript
export interface ChatMessageProps {
  message: {
    role: 'user' | 'assistant';
    content: string;
    timestamp: string;
    model?: string;
    tokens_used?: number;
  };
}

// Features:
- Show message role with icon
- Render markdown content
- Show timestamp
- Copy button
- Show model used (assistant only)
- Show token count (assistant only)
```

**UI:**

```
User Message:
┌────────────────────────────────┐
│ 👤 You                     2:34 │
│ What's the weather today?       │
└────────────────────────────────┘

Assistant Message:
┌────────────────────────────────┐
│ 🤖 Mistral (ollama)        2:35 │
│ I don't have real-time weather  │
│ data, but I can help explain... │
│ [Copy] Tokens: 145              │
└────────────────────────────────┘
```

---

### 7. ChatInput.jsx

**Purpose:** Message input form with send button  
**Props:** None  
**State:** `inputValue`, `isSubmitting`

```javascript
// Features:
- Multi-line input (auto-expand on Enter)
- Send on Ctrl+Enter or button click
- Disable while sending
- Show loading indicator
- Character counter (optional)
- Attach files (future)
```

**Key Implementation:**

```javascript
const handleKeyPress = (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
};
```

---

### 8. ModelSelector.jsx

**Purpose:** Dropdown to select chat model  
**Props:** `selectedModel`, `onModelChange`

```javascript
// Features:
- Show available models with status
- Group by provider (Ollama, OpenAI, Claude, Gemini)
- Show model latency/status indicator
- Warn if model unavailable
- Remember selection in conversation

Available Models:
┌──────────────────────────────┐
│ 🏠 Local (Ollama)            │
│  • Mistral (fast)     ✓       │
│  • Neural Chat                │
│  • Phi (offline)     ✗        │
├──────────────────────────────┤
│ 🔴 OpenAI                    │
│  • GPT-4             (needs key)
│  • GPT-3.5           (needs key)
├──────────────────────────────┤
│ ⭐ Anthropic (Claude)        │
│  • Claude 3          (needs key)
└──────────────────────────────┘
```

---

### 9. ConversationHeader.jsx

**Purpose:** Show conversation title and options  
**Props:** `conversation`

```javascript
// Features:
- Show conversation title/timestamp
- Current model indicator
- Rename conversation button
- Export conversation button
- Delete conversation button
- New conversation button
```

---

## Hook Specifications

### useChat.js (Main Hook)

```javascript
export function useChat() {
  return {
    // State
    conversations, // All conversations
    currentConversationId, // Current active
    currentConversation, // Full object
    messages, // Messages in current
    selectedModel, // Selected model
    isLoading, // Sending message
    error, // Last error

    // Conversation actions
    createNewConversation, // () => void
    setSelectedConversation, // (id) => void
    renameConversation, // (id, name) => void
    deleteConversation, // (id) => void

    // Message actions
    sendMessage, // (text) => Promise
    loadConversationHistory, // (id) => Promise
    clearConversation, // (id) => Promise

    // Model actions
    setSelectedModel, // (model) => void
    getAvailableModels, // () => Promise<Model[]>

    // UI state
    isSidebarOpen, // Boolean
    toggleSidebar, // () => void
  };
}
```

### Implementation Notes:

- Use Zustand store for persistence
- Fetch conversation list on mount
- Cache models list (refresh every 5 min)
- Handle session/auth errors gracefully

---

## Service Layer

### chatService.js (API Calls)

```javascript
export const chatService = {
  // Send message and get response
  async sendMessage(conversationId, message, model, options) {
    // POST /api/chat
    // Returns: { response, model, conversationId, timestamp, tokens_used }
  },

  // Get conversation history
  async getHistory(conversationId) {
    // GET /api/chat/history/{conversationId}
    // Returns: Array of messages
  },

  // Get available models
  async getModels() {
    // GET /api/chat/models
    // Returns: Array of model objects
  },

  // Clear conversation
  async clearHistory(conversationId) {
    // DELETE /api/chat/history/{conversationId}
    // Returns: { success: true }
  },
};
```

---

## Zustand Store Integration

### Update useStore.js

```javascript
const useStore = create((set) => ({
  // Existing state...

  // NEW: Chat state
  chat: {
    conversations: [], // Array of { id, name, createdAt, updatedAt, lastMessage }
    currentConversationId: null,
    messages: [], // Current conversation messages
    selectedModel: 'ollama', // Default model
    isLoading: false,
    error: null,
  },

  // Chat actions
  setChat: (partial) =>
    set((state) => ({
      chat: { ...state.chat, ...partial },
    })),

  setCurrentConversation: (id) =>
    set((state) => ({
      chat: { ...state.chat, currentConversationId: id },
    })),

  addMessage: (message) =>
    set((state) => ({
      chat: { ...state.chat, messages: [...state.chat.messages, message] },
    })),

  clearChat: () =>
    set((state) => ({
      chat: {
        conversations: [],
        currentConversationId: null,
        messages: [],
        selectedModel: 'ollama',
        isLoading: false,
        error: null,
      },
    })),
}));
```

---

## Integration with OversightHub.jsx

### 1. Add navigation item:

```javascript
const navigationItems = [
  // ... existing items
  { label: 'Chat', icon: '💬', path: 'chat' },
  // ... other items
];
```

### 2. Add chat route to currentPage switch:

```javascript
{
  currentPage === 'chat' && <ChatContainer />;
}
```

### 3. Import ChatContainer at top:

```javascript
import ChatContainer from './components/chat/ChatContainer';
```

---

## Styling Strategy

### Main CSS File: chat.css

```css
/* Layout */
.chat-container {
  display: flex;
  height: 100%;
  background: #1a1a1a;
}

.chat-sidebar {
  width: 280px;
  border-right: 1px solid #333;
  overflow-y: auto;
  padding: 15px 10px;
}

.chat-main {
  flex: 1;
  display: flex;
  flex-direction: column;
}

/* Messages */
.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 15px;
}

.chat-message {
  display: flex;
  gap: 10px;
  animation: slideIn 0.3s ease-out;
}

.chat-message.user {
  justify-content: flex-end;
}

.chat-message.assistant {
  justify-content: flex-start;
}

/* Responsive */
@media (max-width: 768px) {
  .chat-sidebar {
    position: absolute;
    width: 250px;
    height: 100%;
    z-index: 100;
    transform: translateX(-100%);
  }

  .chat-sidebar.open {
    transform: translateX(0);
  }
}
```

---

## Testing Strategy

### Unit Tests (chatService.js)

- Mock API calls
- Test error handling
- Test model selection logic

### Component Tests (Chat\*.jsx)

- Test message rendering
- Test input submission
- Test sidebar interactions
- Test model selection

### Integration Tests

- Full chat flow: send → receive → display
- Conversation switching
- Model changing mid-conversation

---

## Performance Considerations

1. **Virtualization:** Use `react-window` for large message lists
2. **Debouncing:** Debounce model selection API calls
3. **Pagination:** Load older messages on scroll
4. **Memoization:** Use `React.memo` for ChatMessage
5. **Caching:** Cache model list for 5 minutes

---

## Error Handling

### API Errors:

- Network timeout → Show retry button
- Invalid model → Fall back to default
- Auth expired → Redirect to login
- Rate limited → Show queue indicator

### UI Errors:

- Graceful degradation if WebSocket fails
- Fallback to polling if needed
- Clear error messages to user

---

## Future Enhancements

1. **Voice Input/Output** - Speech-to-text & TTS
2. **File Upload** - Attach documents for context
3. **Code Execution** - Run code snippets in response
4. **Conversation Branching** - Explore alternate paths
5. **Prompt Templates** - Save/reuse prompts
6. **Team Collaboration** - Share conversations
7. **Analytics** - Token usage, cost per conversation

---

## Development Checklist

- [ ] Create folder structure
- [ ] Implement useChat hook
- [ ] Create chatService.js
- [ ] Build ChatContainer.jsx
- [ ] Build ChatSidebar.jsx
- [ ] Build ChatMain.jsx
- [ ] Build ChatMessages.jsx
- [ ] Build ChatMessage.jsx
- [ ] Build ChatInput.jsx
- [ ] Build ModelSelector.jsx
- [ ] Create chat.css
- [ ] Update useStore.js
- [ ] Update OversightHub.jsx
- [ ] Test all components
- [ ] Performance optimization
- [ ] Error handling
- [ ] Deploy & monitor

---

## Estimated Timeline

| Phase     | Task            | Hours   |
| --------- | --------------- | ------- |
| 1         | Setup & hooks   | 1.5     |
| 2         | Service layer   | 1       |
| 3         | Core components | 2.5     |
| 4         | Integration     | 0.5     |
| 5         | Styling         | 0.5     |
| 6         | Testing         | 1       |
| **Total** |                 | **6.5** |

---

**Document Version:** 1.0  
**Last Updated:** December 8, 2025  
**Status:** Ready for Implementation
