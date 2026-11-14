import { JSONFilePreset } from 'lowdb/node'
import { MongoClient, type Collection, type Db } from 'mongodb'
import type { AIMessage, MessageWithMetadata, WindowedMemoryOptions } from '../types'
import { SYSTEM_PROMPT } from './systemPrompt'
import { v4 as uuidv4 } from 'uuid'
import dotenv from 'dotenv'

dotenv.config()

const DEFAULT_SESSION_ID = process.env['AGENT_SESSION_ID'] || 'default-session'
const DEFAULT_WINDOW_SIZE = parseInt(process.env['MEMORY_WINDOW_SIZE'] || '10', 10)

/**
 * AWS DocumentDB configuration (MongoDB-compatible engine).
 * Example URI:
 * mongodb://masteruser:password@docdb-cluster.cluster-xxxx.us-east-1.docdb.amazonaws.com:27017/?tls=true&tlsCAFile=/opt/ai-agent/certs/global-bundle.pem&replicaSet=rs0&readPreference=secondaryPreferred&retryWrites=false
 */
const DOCDB_URI = process.env['DOCDB_URI'] || process.env['MONGODB_URI'] || ''
const DOCDB_DB_NAME = process.env['DOCDB_DB_NAME'] || 'ai_agent_memory'
const DOCDB_COLLECTION = process.env['DOCDB_COLLECTION'] || 'conversation_history'
const DOCDB_TLS_CA_FILE = process.env['DOCDB_TLS_CA_FILE']

let mongoClient: MongoClient | null = null
let mongoCollection: Collection<MessageWithMetadata> | null = null
let docDbConnectFailed = false

const getDocumentDbCollection = async (): Promise<Collection<MessageWithMetadata> | null> => {
  if (!DOCDB_URI || docDbConnectFailed) {
    return null
  }
  if (mongoCollection) {
    return mongoCollection
  }

  try {
    const options: Record<string, any> = {
      retryWrites: false, // Required for AWS DocumentDB
      serverSelectionTimeoutMS: 3000,
    }
    if (DOCDB_TLS_CA_FILE) {
      options.tls = true
      options.tlsCAFile = DOCDB_TLS_CA_FILE
    }

    mongoClient = new MongoClient(DOCDB_URI, options)
    await mongoClient.connect()
    const db: Db = mongoClient.db(DOCDB_DB_NAME)
    mongoCollection = db.collection<MessageWithMetadata>(DOCDB_COLLECTION)

    // Ensure composite index on (sessionId, createdAt) for fast windowed queries in DocumentDB
    await mongoCollection.createIndex({ sessionId: 1, createdAt: 1 })
    return mongoCollection
  } catch (err) {
    console.warn(
      `[Memory] AWS DocumentDB connection unavailable (${(err as Error).message}). Falling back to local JSON store.`
    )
    docDbConnectFailed = true
    return null
  }
}

export const addMetadata = (
  message: AIMessage,
  sessionId: string = DEFAULT_SESSION_ID
): MessageWithMetadata => ({
  ...message,
  id: uuidv4(),
  sessionId,
  createdAt: new Date().toISOString(),
})

export const removeMetadata = (message: MessageWithMetadata): AIMessage => {
  const { id, sessionId, createdAt, windowIndex, _id, ...messageWithoutMetadata } =
    message as MessageWithMetadata & { _id?: unknown }
  return messageWithoutMetadata as AIMessage
}

type LocalData = {
  messages: MessageWithMetadata[]
}

const defaultData: LocalData = { messages: [] }

export const getLocalDb = async () => {
  return JSONFilePreset<LocalData>('db.json', defaultData)
}

/**
 * Persists messages to AWS DocumentDB (or local fallback store) with session & timestamp metadata.
 */
export const addMessages = async (
  messages: AIMessage[],
  sessionId: string = DEFAULT_SESSION_ID
) => {
  const enriched = messages.map((m) => addMetadata(m, sessionId))
  const collection = await getDocumentDbCollection()

  if (collection) {
    if (enriched.length > 0) {
      await collection.insertMany(enriched)
    }
    return
  }

  const db = await getLocalDb()
  db.data.messages.push(...enriched)
  await db.write()
}

/**
 * Retrieves the complete un-windowed conversation history for a session from AWS DocumentDB.
 */
export const getMessages = async (
  sessionId: string = DEFAULT_SESSION_ID
): Promise<AIMessage[]> => {
  const collection = await getDocumentDbCollection()

  if (collection) {
    const docs = await collection
      .find({ sessionId })
      .sort({ createdAt: 1 })
      .toArray()
    return docs.map(removeMetadata)
  }

  const db = await getLocalDb()
  return db.data.messages
    .filter((m) => (m.sessionId || DEFAULT_SESSION_ID) === sessionId)
    .map(removeMetadata)
}

/**
 * Applies a sliding conversation window while preserving OpenAI tool-calling sequence integrity.
 * Never starts a window with an orphaned `role: 'tool'` message without its preceding `assistant` tool_calls message.
 */
export const applyConversationWindow = (
  messages: AIMessage[],
  windowSize: number = DEFAULT_WINDOW_SIZE,
  includeSystemPrompt: boolean = true
): AIMessage[] => {
  if (windowSize <= 0 || messages.length <= windowSize) {
    const base = [...messages]
    if (includeSystemPrompt && (base.length === 0 || base[0].role !== 'system')) {
      return [{ role: 'system', content: SYSTEM_PROMPT }, ...base]
    }
    return base
  }

  let startIndex = Math.max(0, messages.length - windowSize)

  // Walk backward if the window cut landed on a `tool` message so its parent `assistant` tool_calls is included
  while (startIndex > 0 && messages[startIndex].role === 'tool') {
    startIndex--
  }

  const windowedSlice = messages.slice(startIndex)

  if (
    includeSystemPrompt &&
    (windowedSlice.length === 0 || windowedSlice[0].role !== 'system')
  ) {
    return [{ role: 'system', content: SYSTEM_PROMPT }, ...windowedSlice]
  }

  return windowedSlice
}

/**
 * Retrieves windowed persistent conversation history from AWS DocumentDB.
 */
export const getWindowedMessages = async (
  options: WindowedMemoryOptions = {}
): Promise<AIMessage[]> => {
  const sessionId = options.sessionId || DEFAULT_SESSION_ID
  const windowSize = options.windowSize ?? DEFAULT_WINDOW_SIZE
  const includeSystemPrompt = options.includeSystemPrompt ?? true

  const collection = await getDocumentDbCollection()

  if (collection) {
    // Fetch an extra buffer (+6) from DocumentDB in descending order to ensure tool_call boundaries stay intact
    const fetchLimit = Math.max(windowSize + 6, 12)
    const recentDocs = await collection
      .find({ sessionId })
      .sort({ createdAt: -1 })
      .limit(fetchLimit)
      .toArray()

    const ordered = recentDocs.reverse().map(removeMetadata)
    return applyConversationWindow(ordered, windowSize, includeSystemPrompt)
  }

  const allSessionMessages = await getMessages(sessionId)
  return applyConversationWindow(allSessionMessages, windowSize, includeSystemPrompt)
}

export const saveToolResponse = async (
  toolCallId: string,
  toolResponse: string,
  sessionId: string = DEFAULT_SESSION_ID
) => {
  return addMessages(
    [
      {
        role: 'tool',
        content: toolResponse,
        tool_call_id: toolCallId,
      },
    ],
    sessionId
  )
}

export const clearSessionHistory = async (
  sessionId: string = DEFAULT_SESSION_ID
) => {
  const collection = await getDocumentDbCollection()
  if (collection) {
    await collection.deleteMany({ sessionId })
    return
  }

  const db = await getLocalDb()
  db.data.messages = db.data.messages.filter(
    (m) => (m.sessionId || DEFAULT_SESSION_ID) !== sessionId
  )
  await db.write()
}
