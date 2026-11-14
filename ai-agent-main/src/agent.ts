import type { AgentRunOptions, AIMessage, GenericToolDefinition } from '../types'
import {
  addMessages,
  getMessages,
  getWindowedMessages,
  saveToolResponse,
} from './memory'
import { runLLM } from './llm'
import { showLoader, logMessage } from './ui'
import { runTool } from './toolRunner'
import { toolRegistry } from './toolRegistry'
import './tools/customApis'

const DEFAULT_MAX_ITERATIONS = 10

/**
 * Automated Switchable Generic Tool-Calling Loop:
 * - Supports switching seamlessly between:
 *   1. 'hybrid' (default): TypeScript agent loop + auto-synced Python FastAPI tools + TS custom APIs
 *   2. 'typescript': TypeScript agent loop using local & TS REST API tools
 *   3. 'python-fastapi': Delegates the entire agent execution loop to the Python FastAPI backend (/api/v1/agent/chat)
 * - Uses windowed persistent conversation history stored in AWS DocumentDB.
 */
export const runAgent = async ({
  userMessage,
  tools,
  sessionId = process.env['AGENT_SESSION_ID'] || 'default-session',
  windowSize = parseInt(process.env['MEMORY_WINDOW_SIZE'] || '10', 10),
  mode = (process.env['AGENT_EXECUTION_MODE'] as any) || 'hybrid',
  maxIterations = DEFAULT_MAX_ITERATIONS,
  fastApiBaseUrl = process.env['FASTAPI_BASE_URL'] || 'http://127.0.0.1:8000',
}: AgentRunOptions): Promise<AIMessage[]> => {
  // Option A: Delegate full agent loop to Python FastAPI Backend when mode === 'python-fastapi'
  if (mode === 'python-fastapi') {
    const loader = showLoader('🐍 Running agent loop on Python FastAPI backend...')
    try {
      const res = await fetch(`${fastApiBaseUrl.replace(/\/$/, '')}/api/v1/agent/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: userMessage,
          session_id: sessionId,
          window_size: windowSize,
        }),
      })

      if (!res.ok) {
        throw new Error(`FastAPI backend returned HTTP ${res.status}`)
      }

      const data = (await res.json()) as {
        reply: string
        history: AIMessage[]
      }
      loader.stop()
      logMessage({ role: 'assistant', content: data.reply })
      return data.history
    } catch (err) {
      loader.fail(
        `FastAPI backend unreachable (${(err as Error).message}); switching automatically to local TypeScript loop.`
      )
    }
  }

  // Option B: Hybrid or TypeScript-driven Automated Generic Tool-Calling Loop
  if (mode === 'hybrid') {
    await toolRegistry.syncFastApiTools(fastApiBaseUrl)
  }

  // Register any extra tools passed directly to runAgent()
  if (tools && tools.length > 0) {
    for (const t of tools) {
      if (!toolRegistry.getTool(t.name)) {
        toolRegistry.registerTool({
          ...t,
          source: t.source || 'local-ts',
        } as GenericToolDefinition)
      }
    }
  }

  const activeTools =
    tools && tools.length > 0
      ? tools
      : toolRegistry.getToolsForMode(mode === 'hybrid' ? 'hybrid' : 'typescript')

  await addMessages([{ role: 'user', content: userMessage }], sessionId)

  const loader = showLoader('🤔 Thinking (GPT-4o mini via GitHub Models)...')
  let iteration = 0

  while (iteration < maxIterations) {
    iteration++

    // Fetch windowed persistent conversation history from AWS DocumentDB
    const windowedHistory = await getWindowedMessages({
      sessionId,
      windowSize,
      includeSystemPrompt: true,
    })

    const response = await runLLM({
      messages: windowedHistory,
      tools: activeTools,
    })

    await addMessages([response], sessionId)

    if (response.tool_calls && response.tool_calls.length > 0) {
      logMessage(response)

      for (const toolCall of response.tool_calls) {
        loader.update(`executing tool [${mode}]: ${toolCall.function.name}`)
        const toolResponse = await runTool(
          toolCall,
          userMessage,
          sessionId,
          activeTools
        )
        await saveToolResponse(toolCall.id, toolResponse, sessionId)
        loader.update(`completed tool: ${toolCall.function.name}`)
      }
      continue
    }

    if (response.content) {
      loader.stop()
      logMessage(response)
      return getMessages(sessionId)
    }
  }

  loader.stop()
  return getMessages(sessionId)
}
