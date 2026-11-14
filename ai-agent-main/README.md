# Cloud-Native AI Agent (TypeScript FE/CLI + Python FastAPI BE)

Engineered a **TypeScript and Python AI Agent** utilizing **OpenAI GPT-4o mini via GitHub Models** (`https://models.github.ai/inference`) with an **automated, switchable generic tool-calling loop** and **windowed persistent conversation history stored in AWS DocumentDB**, alongside **AWS RDS PostgreSQL** and **RESTful APIs**.

---

## Key Highlights

1. **OpenAI GPT-4o mini via GitHub Models (`src/ai.ts`, `src/llm.ts`, `backend/app/agent.py`)**:
   - Unified inference client targeting `https://models.github.ai/inference` with model `openai/gpt-4o-mini` authenticated via `GITHUB_TOKEN`.
2. **Automated Switchable Generic Tool-Calling Loop (`src/agent.ts`, `src/toolRegistry.ts`, `backend/app/tool_registry.py`)**:
   - Switch seamlessly between 3 execution modes via CLI flag (`--mode=`), `.env` (`AGENT_EXECUTION_MODE`), or the Web UI dropdown:
     - **`hybrid` (Default)**: TypeScript runs the tool-calling loop, executing local TypeScript tools (`src/tools/customApis.ts`) AND automatically discovering/invoking tools exposed by the Python FastAPI backend (`GET /api/v1/tools` & `POST /api/v1/tools/{name}/execute`).
     - **`python-fastapi`**: Delegates the entire multi-turn tool-calling loop to the Python FastAPI backend (`POST /api/v1/agent/chat`).
     - **`typescript`**: Standalone TypeScript tool-calling loop executing local functions and TypeScript-configured REST APIs.
3. **Effortless API Customization in Python FastAPI & TypeScript FE**:
   - **In Python FastAPI (`backend/app/tools/custom_apis.py`)**: Decorate any async/sync Python function with `@tool_registry.register_api(name=..., description=..., args_schema=...)` (and `@router.post(...)`). It automatically generates the OpenAI JSON Schema, exposes a REST endpoint, and registers the tool for both Python and TypeScript agent loops.
   - **In TypeScript FE (`src/tools/customApis.ts`)**: Call `defineCustomApiTool({ name, description, parameters: z.object(...), endpoint, method })` or `toolRegistry.registerLocalTool(...)`.
   - **At Runtime via REST API / Web UI**: `POST /api/v1/tools/register` dynamically adds any external REST API tool and persists it in **AWS RDS PostgreSQL**.
4. **Windowed Persistent Conversation History in AWS DocumentDB (`src/memory.ts`, `backend/app/documentdb.py`)**:
   - Persists every conversation turn with session & timestamp metadata in **AWS DocumentDB** (with TLS CA bundle support and `retryWrites=false`, plus automatic local `db.json` fallback for offline dev).
   - Implements **tool-call-safe sliding window context retrieval** (`getWindowedMessages` / `get_windowed_history`) that bounds prompt token usage while guaranteeing `assistant` `tool_calls` and `tool` response messages are never orphaned across window boundaries.

---

## Quick Start

### 1. Configure Environment (`.env`)
```bash
cp .env.example .env
# Set GITHUB_TOKEN=your_github_models_pat
```

### 2. Run the Python FastAPI Backend & Web UI
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000
```
- Open `http://localhost:8000` for the interactive **Cloud-Native AI Agent Web UI** (live loop mode switcher, DocumentDB sliding window inspector, and dynamic REST API tool registration).
- Open `http://localhost:8000/docs` for the interactive **FastAPI OpenAPI / Swagger UI**.

### 3. Run the TypeScript Agent CLI
```bash
npm install

# Run in Hybrid mode (TS loop + auto-discovered Python FastAPI tools)
npm run start -- "What is the weather in Boston and what is our RDS PostgreSQL status?"

# Run in pure TypeScript mode
npm run agent:ts -- "Check the weather in New York"

# Run in Python FastAPI mode from TypeScript CLI
npm run agent:python -- "Inspect our DocumentDB conversation window stats"
```
