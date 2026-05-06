"""
Global configuration and settings for the JUnit Generator Pipeline.
"""
import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # LLM Configuration
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "ollama")   # ollama | openai | anthropic
    LLM_MODEL: str = os.getenv("LLM_MODEL", "llama3.2")        # ollama: llama3.2, codellama, etc.
    LLM_TEMPERATURE: float = float(os.getenv("LLM_TEMPERATURE", "0.2"))
    OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

    # Optional cloud LLM keys (only needed if LLM_PROVIDER != ollama)
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")

    # Bitbucket Configuration
    BITBUCKET_USERNAME: str = os.getenv("BITBUCKET_USERNAME", "")
    BITBUCKET_APP_PASSWORD: str = os.getenv("BITBUCKET_APP_PASSWORD", "")
    BITBUCKET_WORKSPACE: str = os.getenv("BITBUCKET_WORKSPACE", "")
    CLONE_BASE_DIR: str = os.getenv("CLONE_BASE_DIR", "/tmp/junit_pipeline_repos")

    # Pipeline Thresholds
    COVERAGE_THRESHOLD: float = float(os.getenv("COVERAGE_THRESHOLD", "80.0"))
    TEST_PASS_THRESHOLD: float = float(os.getenv("TEST_PASS_THRESHOLD", "80.0"))
    MAX_COVERAGE_ITERATIONS: int = int(os.getenv("MAX_COVERAGE_ITERATIONS", "5"))
    MAX_TEST_PASS_ITERATIONS: int = int(os.getenv("MAX_TEST_PASS_ITERATIONS", "5"))

    # MCP Server Configuration
    MCP_HOST: str = os.getenv("MCP_HOST", "localhost")
    MCP_PORT: int = int(os.getenv("MCP_PORT", "8765"))

    # Maven / Java
    MAVEN_CMD: str = os.getenv("MAVEN_CMD", "mvn")
    JAVA_HOME: str = os.getenv("JAVA_HOME", "")


settings = Settings()
