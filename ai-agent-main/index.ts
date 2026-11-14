import 'dotenv/config'
import { runAgent } from './src/agent'
import { DEFAULT_CUSTOM_TOOLS } from './src/tools/customApis'
import type { AgentExecutionMode } from './types'

const args = process.argv.slice(2)
const modeArg = args.find((a) => a.startsWith('--mode='))?.split('=')[1] as
  | AgentExecutionMode
  | undefined
const sessionArg = args.find((a) => a.startsWith('--session='))?.split('=')[1]
const windowArg = args.find((a) => a.startsWith('--window='))?.split('=')[1]
const userMessage = args.find((a) => !a.startsWith('--'))

if (!userMessage) {
  console.error(
    'Usage: npm run start -- "<message>" [--mode=hybrid|typescript|python-fastapi] [--session=<id>] [--window=<size>]'
  )
  process.exit(1)
}

const response = await runAgent({
  userMessage,
  tools: DEFAULT_CUSTOM_TOOLS,
  mode: modeArg || (process.env['AGENT_EXECUTION_MODE'] as AgentExecutionMode) || 'hybrid',
  sessionId: sessionArg || process.env['AGENT_SESSION_ID'] || 'default-session',
  windowSize: windowArg ? parseInt(windowArg, 10) : 10,
})

response.forEach((msg: { role: string; content?: any }) => {
  let contentStr = ''
  if (typeof msg.content === 'string') {
    contentStr = msg.content
  } else if (Array.isArray(msg.content)) {
    contentStr = msg.content.map((part: any) => part.text ?? '').join(' ')
  } else if (msg.content != null) {
    contentStr = String(msg.content)
  }
  if (contentStr) {
    console.log(`${msg.role}: ${contentStr}`)
  }
})
