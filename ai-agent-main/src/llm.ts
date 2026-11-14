import dotenv from 'dotenv'
import { openai, DEFAULT_MODEL, assertGitHubToken } from './ai'
import type OpenAI from 'openai'
import { zodFunction } from 'openai/helpers/zod'
import type { GenericToolDefinition } from '../types'

dotenv.config()

/**
 * Formats both Zod-defined TypeScript tools AND JSON-Schema-defined Python FastAPI tools
 * into OpenAI ChatCompletionTool definitions for GPT-4o-mini via GitHub Models.
 */
export const formatToolsForOpenAI = (
  tools: any[]
): OpenAI.Chat.Completions.ChatCompletionTool[] => {
  return tools.map((tool: GenericToolDefinition | any) => {
    if (tool.parameters) {
      return zodFunction({
        name: tool.name,
        description: tool.description,
        parameters: tool.parameters,
      })
    }

    if (tool.jsonSchema) {
      return {
        type: 'function',
        function: {
          name: tool.name,
          description: tool.description,
          parameters: tool.jsonSchema,
        },
      }
    }

    return zodFunction(tool)
  })
}

export const runLLM = async ({
  messages,
  tools,
  model = DEFAULT_MODEL,
}: {
  messages: OpenAI.Chat.Completions.ChatCompletionMessageParam[]
  tools: any[]
  model?: string
}) => {
  assertGitHubToken()
  const formattedTools = formatToolsForOpenAI(tools)

  const response = await openai.chat.completions.create({
    model,
    temperature: 0.1,
    messages,
    ...(formattedTools.length > 0
      ? {
          tools: formattedTools,
          tool_choice: 'auto',
          parallel_tool_calls: false,
        }
      : {}),
  })

  return response.choices[0].message
}
