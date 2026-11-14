import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # GitHub Models / OpenAI GPT-4o mini
    GITHUB_TOKEN: str = os.getenv("GITHUB_TOKEN", "")
    GITHUB_MODELS_ENDPOINT: str = os.getenv(
        "GITHUB_MODELS_ENDPOINT", "https://models.github.ai/inference"
    )
    AI_MODEL: str = os.getenv("AI_MODEL", "openai/gpt-4o-mini")

    # AWS DocumentDB (Windowed Persistent Conversation History)
    DOCDB_URI: str = os.getenv("DOCDB_URI", os.getenv("MONGODB_URI", ""))
    DOCDB_DB_NAME: str = os.getenv("DOCDB_DB_NAME", "ai_agent_memory")
    DOCDB_COLLECTION: str = os.getenv("DOCDB_COLLECTION", "conversation_history")
    DOCDB_TLS_CA_FILE: str = os.getenv("DOCDB_TLS_CA_FILE", "")
    MEMORY_WINDOW_SIZE: int = int(os.getenv("MEMORY_WINDOW_SIZE", "10"))

    # AWS RDS PostgreSQL
    DATABASE_HOST: str = os.getenv("DATABASE_HOST", "localhost")
    DATABASE_PORT: str = os.getenv("DATABASE_PORT", "5432")
    DATABASE_NAME: str = os.getenv("DATABASE_NAME", "webapp")
    DATABASE_USER: str = os.getenv("DATABASE_USER", "webapp")
    DATABASE_PASSWORD: str = os.getenv("DATABASE_PASSWORD", "password")
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        f"postgresql+pg8000://{DATABASE_USER}:{DATABASE_PASSWORD}@{DATABASE_HOST}:{DATABASE_PORT}/{DATABASE_NAME}",
    )

    # AWS Cloud-Native Eventing (SNS / SQS / Lambda)
    AWS_REGION: str = os.getenv("AWS_REGION", "us-east-1")
    SNS_TOPIC_ARN: str = os.getenv("SNS_TOPIC_ARN", "")
    SQS_QUEUE_URL: str = os.getenv("SQS_QUEUE_URL", "")


settings = Settings()
