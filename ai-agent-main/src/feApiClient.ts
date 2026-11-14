import { z } from 'zod'
import { toolRegistry } from './toolRegistry'
import { defineCustomApiTool } from './tools/customApis'
import type { AgentExecutionMode, AIMessage } from '../types'

export interface CustomApiRegistrationPayload {
  name: string
  description: string
  endpoint: string
  method: 'GET' | 'POST' | 'PUT' | 'DELETE' | 'PATCH'
  parametersSchema: Record<string, any>
  headers?: Record<string, string>
}

/**
 * TypeScript Frontend SDK for interacting with the Python FastAPI Backend,
 * switching tool-calling loop execution modes, and registering custom APIs across FE & BE.
 */
export class AgentFrontendClient {
  private baseUrl: string

  constructor(
    baseUrl: string = process.env['FASTAPI_BASE_URL'] || 'http://127.0.0.1:8000'
  ) {
    this.baseUrl = baseUrl.replace(/\/$/, '')
  }

  /**
   * Send a chat message to the AI Agent with configurable execution mode and DocumentDB window size.
   */
  public async sendMessage(params: {
    message: string
    sessionId?: string
    windowSize?: number
    mode?: AgentExecutionMode
  }): Promise<{
    reply: string
    mode: AgentExecutionMode
    toolCallsExecuted: Array<{ name: string; args: any; result: any }>
    windowedCount: number
    totalStoredCount: number
    history: AIMessage[]
  }> {
    const response = await fetch(`${this.baseUrl}/api/v1/agent/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: params.message,
        session_id: params.sessionId || 'default-session',
        window_size: params.windowSize ?? 10,
        execution_mode: params.mode || 'python-fastapi',
      }),
    })

    if (!response.ok) {
      throw new Error(`Agent chat request failed with HTTP ${response.status}`)
    }

    return response.json()
  }

  /**
   * Dynamically register a custom REST API tool in both TypeScript FE and Python FastAPI BE.
   */
  public async registerCustomApi(payload: CustomApiRegistrationPayload) {
    // 1. Register in local TypeScript FE tool registry
    defineCustomApiTool({
      name: payload.name,
      description: payload.description,
      parameters: z.record(z.any()),
      endpoint: payload.endpoint,
      method: payload.method,
      headers: payload.headers,
    })

    // 2. Persist in Python FastAPI Backend + RDS PostgreSQL tool catalog
    const response = await fetch(`${this.baseUrl}/api/v1/tools/register`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: payload.name,
        description: payload.description,
        endpoint_url: payload.endpoint,
        http_method: payload.method,
        parameters_schema: payload.parametersSchema,
        headers: payload.headers || {},
      }),
    })

    if (!response.ok) {
      throw new Error(`Failed to register custom API in FastAPI backend: ${response.status}`)
    }

    return response.json()
  }

  /**
   * List all tools currently available in the switchable generic tool-calling loop.
   */
  public async listAvailableTools() {
    await toolRegistry.syncFastApiTools(this.baseUrl)
    return toolRegistry.getToolsForMode('hybrid')
  }

  /**
   * Fetch windowed vs. full conversation history stored in AWS DocumentDB.
   */
  public async getConversationHistory(sessionId: string = 'default-session', windowSize: number = 10) {
    const response = await fetch(
      `${this.baseUrl}/api/v1/conversations/${encodeURIComponent(sessionId)}?window_size=${windowSize}`
    )
    if (!response.ok) {
      throw new Error(`Failed to fetch DocumentDB history: HTTP ${response.status}`)
    }
    return response.json()
  }
}
