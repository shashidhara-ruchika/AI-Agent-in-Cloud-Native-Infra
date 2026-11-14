import OpenAI from 'openai'
import dotenv from 'dotenv'

dotenv.config()

const token = process.env['GITHUB_TOKEN'] || 'ghp_placeholder_token_for_dev'

export const GITHUB_MODELS_ENDPOINT =
  process.env['GITHUB_MODELS_ENDPOINT'] || 'https://models.github.ai/inference'

export const DEFAULT_MODEL = process.env['AI_MODEL'] || 'openai/gpt-4o-mini'

export const assertGitHubToken = () => {
  if (!process.env['GITHUB_TOKEN']) {
    throw new Error(
      'GITHUB_TOKEN is not set. Add GITHUB_TOKEN to your .env file to authenticate with GitHub Models (openai/gpt-4o-mini).'
    )
  }
}

export const openai = new OpenAI({
  baseURL: GITHUB_MODELS_ENDPOINT,
  apiKey: token,
})
