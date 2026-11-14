import type OpenAI from 'openai'
import type { ZodTypeAny } from 'zod'

export type AIMessage =
  | OpenAI.Chat.Completions.ChatCompletionAssistantMessageParam
  | OpenAI.Chat.Completions.ChatCompletionSystemMessageParam
  | { role: 'user'; content: string }
  | { role: 'tool'; content: string; tool_call_id: string }

export interface ToolFn<A = any, T = any> {
  (input: { userMessage: string; toolArgs: A; sessionId?: string }): Promise<T> | T
}

/**
 * Switchable execution modes for the AI Agent tool-calling loop:
 * - 'hybrid': TypeScript runs the LLM loop & executes both local TS tools and remote Python FastAPI tools
 * - 'typescript': TypeScript runs the LLM loop using local TS tools and TS-configured REST APIs
 * - 'python-fastapi': Delegates the entire LLM & tool-calling loop to the Python FastAPI backend (/api/v1/agent/chat)
 */
export type AgentExecutionMode = 'hybrid' | 'typescript' | 'python-fastapi'

export type ToolSource = 'local-ts' | 'ts-rest-api' | 'python-fastapi'

export interface GenericToolDefinition<TParams extends ZodTypeAny = ZodTypeAny> {
  name: string
  description: string
  parameters?: TParams
  /** Raw JSON schema when synced dynamically from Python FastAPI backend */
  jsonSchema?: Record<string, any>
  source: ToolSource
  /** Direct execution handler for local TypeScript tools */
  execute?: ToolFn<any, any>
  /** REST API configuration for custom HTTP / FastAPI endpoints */
  apiConfig?: RestApiToolConfig
}

export interface RestApiToolConfig {
  url: string
  method: 'GET' | 'POST' | 'PUT' | 'DELETE' | 'PATCH'
  headers?: Record<string, string>
  timeoutMs?: number
  transformResponse?: (data: any) => string
}

export interface WindowedMemoryOptions {
  sessionId?: string
  windowSize?: number
  includeSystemPrompt?: boolean
}

export type MessageWithMetadata = AIMessage & {
  id: string
  sessionId: string
  createdAt: string
  windowIndex?: number
}

export interface AgentRunOptions {
  userMessage: string
  tools?: GenericToolDefinition[]
  sessionId?: string
  windowSize?: number
  mode?: AgentExecutionMode
  maxIterations?: number
  fastApiBaseUrl?: string
}
