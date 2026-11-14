import { z } from 'zod'
import { toolRegistry } from '../toolRegistry'
import type { GenericToolDefinition, RestApiToolConfig } from '../../types'

const FASTAPI_BASE_URL =
  process.env['FASTAPI_BASE_URL'] || 'http://127.0.0.1:8000'

/**
 * Helper to quickly add a new custom REST API tool in TypeScript FE.
 * Add your custom API definitions below and they will automatically be wired into
 * the OpenAI GPT-4o-mini tool-calling loop.
 */
export const defineCustomApiTool = <T extends z.ZodTypeAny>(config: {
  name: string
  description: string
  parameters: T
  endpoint: string
  method?: RestApiToolConfig['method']
  headers?: Record<string, string>
  transformResponse?: (data: any) => string
}): GenericToolDefinition<T> => {
  const fullUrl = config.endpoint.startsWith('http')
    ? config.endpoint
    : `${FASTAPI_BASE_URL.replace(/\/$/, '')}/${config.endpoint.replace(/^\//, '')}`

  return toolRegistry.registerApiTool({
    name: config.name,
    description: config.description,
    parameters: config.parameters,
    apiConfig: {
      url: fullUrl,
      method: config.method || 'POST',
      headers: config.headers,
      transformResponse: config.transformResponse,
    },
  })
}

/**
 * 1. Local TypeScript Tool: Weather Lookup
 */
export const weatherTool = toolRegistry.registerLocalTool({
  name: 'get_weather',
  description: 'Get current weather conditions and temperature for a location.',
  parameters: z.object({
    location: z
      .string()
      .default('Boston, MA')
      .describe('City and state/country to check weather for'),
    reasoning: z.string().describe('Why did you pick this tool?'),
  }),
  execute: async ({ toolArgs }) => {
    const loc = toolArgs.location || 'Boston, MA'
    return JSON.stringify({
      location: loc,
      temperature: '72°F (22°C)',
      condition: 'Clear sky, light breeze',
      source: 'local-ts-tool',
    })
  },
})

/**
 * 2. Customizable REST API Tool -> Python FastAPI RDS PostgreSQL Status & Query API
 */
export const rdsPostgresStatusTool = defineCustomApiTool({
  name: 'check_rds_postgres_status',
  description:
    'Queries the Python FastAPI backend to inspect AWS RDS PostgreSQL relational database status, user verification metrics, and recent tool audit logs.',
  parameters: z.object({
    includeAuditLogs: z
      .boolean()
      .default(true)
      .describe('Whether to include recent agent execution audit logs from RDS PostgreSQL'),
    reasoning: z.string().describe('Why did you pick this tool?'),
  }),
  endpoint: '/api/v1/apis/rds-status',
  method: 'POST',
})

/**
 * 3. Customizable REST API Tool -> Trigger AWS SNS / SQS / Lambda Notification Pipeline
 */
export const triggerSnsNotificationTool = defineCustomApiTool({
  name: 'publish_sns_notification',
  description:
    'Publishes an event to the AWS SNS topic (fanned out to AWS SQS and processed by AWS Lambda) for user email verification or async agent alerts.',
  parameters: z.object({
    email: z.string().email().describe('Recipient email address'),
    eventType: z
      .enum(['VERIFY_EMAIL', 'AGENT_TASK_ALERT'])
      .default('AGENT_TASK_ALERT')
      .describe('Type of SNS/SQS event to publish'),
    message: z.string().describe('Notification payload message'),
  }),
  endpoint: '/api/v1/apis/notify-sns',
  method: 'POST',
})

/**
 * 4. Customizable REST API Tool -> Cloud-Native Health Check (/healthz)
 */
export const cloudHealthCheckTool = defineCustomApiTool({
  name: 'get_cloud_infrastructure_health',
  description:
    'Checks the live health of the AWS Cloud-Native stack (FastAPI Backend, RDS PostgreSQL, AWS DocumentDB conversation store, and SNS/SQS connectivity).',
  parameters: z.object({
    reasoning: z.string().describe('Reason for checking cloud infrastructure health'),
  }),
  endpoint: '/healthz',
  method: 'GET',
})

export const DEFAULT_CUSTOM_TOOLS: GenericToolDefinition[] = [
  weatherTool,
  rdsPostgresStatusTool,
  triggerSnsNotificationTool,
  cloudHealthCheckTool,
]
