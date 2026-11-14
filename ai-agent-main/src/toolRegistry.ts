import type { ZodTypeAny } from 'zod'
import type {
  AgentExecutionMode,
  GenericToolDefinition,
  RestApiToolConfig,
  ToolFn,
} from '../types'

class GenericToolRegistry {
  private tools: Map<string, GenericToolDefinition> = new Map()

  /**
   * Register a local TypeScript tool with a Zod parameter schema and execution handler.
   */
  public registerLocalTool<TParams extends ZodTypeAny>(config: {
    name: string
    description: string
    parameters: TParams
    execute: ToolFn<any, any>
  }): GenericToolDefinition<TParams> {
    const tool: GenericToolDefinition<TParams> = {
      name: config.name,
      description: config.description,
      parameters: config.parameters,
      source: 'local-ts',
      execute: config.execute,
    }
    this.tools.set(config.name, tool)
    return tool
  }

  /**
   * Easily register any external or internal RESTful API endpoint from TypeScript FE.
   * When the LLM invokes this tool, the GenericToolRunner automatically performs the HTTP call.
   */
  public registerApiTool<TParams extends ZodTypeAny>(config: {
    name: string
    description: string
    parameters: TParams
    apiConfig: RestApiToolConfig
  }): GenericToolDefinition<TParams> {
    const tool: GenericToolDefinition<TParams> = {
      name: config.name,
      description: config.description,
      parameters: config.parameters,
      source: 'ts-rest-api',
      apiConfig: config.apiConfig,
    }
    this.tools.set(config.name, tool)
    return tool
  }

  /**
   * Automatically discovers and syncs tools exposed by the Python FastAPI backend (`GET /api/v1/tools`).
   * Any new @register_tool_api added in Python FastAPI immediately becomes available to the TypeScript agent!
   */
  public async syncFastApiTools(
    fastApiBaseUrl: string = process.env['FASTAPI_BASE_URL'] || 'http://127.0.0.1:8000'
  ): Promise<GenericToolDefinition[]> {
    try {
      const response = await fetch(`${fastApiBaseUrl.replace(/\/$/, '')}/api/v1/tools`, {
        method: 'GET',
        headers: { Accept: 'application/json' },
        signal: AbortSignal.timeout(3000),
      })

      if (!response.ok) {
        return []
      }

      const payload = (await response.json()) as {
        tools?: Array<{
          name: string
          description: string
          parameters: Record<string, any>
          endpoint?: string
          method?: 'GET' | 'POST' | 'PUT' | 'DELETE' | 'PATCH'
        }>
      }

      const synced: GenericToolDefinition[] = []
      for (const remoteTool of payload.tools || []) {
        const toolDef: GenericToolDefinition = {
          name: remoteTool.name,
          description: remoteTool.description,
          jsonSchema: remoteTool.parameters,
          source: 'python-fastapi',
          apiConfig: {
            url: `${fastApiBaseUrl.replace(/\/$/, '')}/api/v1/tools/${encodeURIComponent(
              remoteTool.name
            )}/execute`,
            method: 'POST',
          },
        }
        this.tools.set(remoteTool.name, toolDef)
        synced.push(toolDef)
      }
      return synced
    } catch {
      // Backend may not be running in standalone CLI mode; silently continue with local & TS REST tools
      return []
    }
  }

  public registerTool(tool: GenericToolDefinition): void {
    this.tools.set(tool.name, tool)
  }

  public unregisterTool(name: string): boolean {
    return this.tools.delete(name)
  }

  public getTool(name: string): GenericToolDefinition | undefined {
    return this.tools.get(name)
  }

  public getToolsForMode(mode: AgentExecutionMode = 'hybrid'): GenericToolDefinition[] {
    const all = Array.from(this.tools.values())
    if (mode === 'typescript') {
      return all.filter((t) => t.source === 'local-ts' || t.source === 'ts-rest-api')
    }
    return all
  }
}

export const toolRegistry = new GenericToolRegistry()
