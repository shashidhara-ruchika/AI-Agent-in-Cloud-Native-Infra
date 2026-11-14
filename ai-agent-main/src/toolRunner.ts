import type OpenAI from 'openai'
import type { GenericToolDefinition } from '../types'
import { toolRegistry } from './toolRegistry'
import './tools/customApis'

/**
 * Automated Switchable Generic Tool Runner.
 * Dynamically dispatches LLM tool calls to:
 *  1. Local TypeScript tool handlers (`source === 'local-ts'`)
 *  2. Custom TypeScript FE REST API endpoints (`source === 'ts-rest-api'`)
 *  3. Auto-discovered Python FastAPI Backend tool endpoints (`source === 'python-fastapi'`)
 */
export const runTool = async (
  toolCall: OpenAI.Chat.Completions.ChatCompletionMessageToolCall,
  userMessage: string,
  sessionId?: string,
  runtimeTools?: GenericToolDefinition[]
): Promise<string> => {
  const toolName = toolCall.function.name
  const rawArgs = JSON.parse(toolCall.function.arguments || '{}')

  // Resolve tool definition from runtime list or central registry
  const toolDef =
    runtimeTools?.find((t) => t.name === toolName) || toolRegistry.getTool(toolName)

  if (!toolDef) {
    throw new Error(
      `Unknown tool "${toolName}". Register it in src/tools/customApis.ts or Python FastAPI backend/app/tools/custom_apis.py.`
    )
  }

  // Validate with Zod schema if provided
  const validatedArgs = toolDef.parameters
    ? toolDef.parameters.parse(rawArgs)
    : rawArgs

  const input = {
    userMessage,
    toolArgs: validatedArgs,
    sessionId,
  }

  // 1. Direct TypeScript handler execution
  if (toolDef.execute) {
    const result = await toolDef.execute(input)
    return typeof result === 'string' ? result : JSON.stringify(result)
  }

  // 2. Generic REST API / Python FastAPI execution
  if (toolDef.apiConfig) {
    const { url, method, headers = {}, timeoutMs = 10000, transformResponse } =
      toolDef.apiConfig

    let requestUrl = url
    const fetchOptions: RequestInit = {
      method,
      headers: {
        'Content-Type': 'application/json',
        Accept: 'application/json',
        ...headers,
      },
      signal: AbortSignal.timeout(timeoutMs),
    }

    if (method === 'GET') {
      const searchParams = new URLSearchParams()
      for (const [k, v] of Object.entries(validatedArgs || {})) {
        if (v !== undefined && v !== null) {
          searchParams.set(k, String(v))
        }
      }
      const qs = searchParams.toString()
      if (qs) {
        requestUrl += (requestUrl.includes('?') ? '&' : '?') + qs
      }
    } else {
      fetchOptions.body = JSON.stringify({
        ...validatedArgs,
        toolArgs: validatedArgs,
        userMessage,
        sessionId,
      })
    }

    try {
      const response = await fetch(requestUrl, fetchOptions)
      const data = await response.json().catch(async () => ({
        status: response.status,
        text: await response.text(),
      }))

      if (!response.ok) {
        return JSON.stringify({
          error: `API returned HTTP ${response.status}`,
          endpoint: requestUrl,
          details: data,
        })
      }

      if (transformResponse) {
        return transformResponse(data)
      }
      return typeof data === 'string' ? data : JSON.stringify(data)
    } catch (error) {
      return JSON.stringify({
        status: 'offline_fallback',
        tool: toolName,
        endpoint: requestUrl,
        message: `Could not reach REST endpoint (${(error as Error).message}). Ensure the Python FastAPI backend is running on ${requestUrl}.`,
        simulatedArgs: validatedArgs,
      })
    }
  }

  throw new Error(`Tool "${toolName}" has neither an execute handler nor an apiConfig.`)
}
