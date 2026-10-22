from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: SecretStr = SecretStr("")
    exa_api_key: SecretStr = SecretStr("")
    llm_model: str = "claude-sonnet-4-5"
    database_path: str = "data/checkpoints.sqlite"
    api_timeout_seconds: float = Field(default=45, gt=0, le=180)

    def require_keys(self):
        if not self.anthropic_api_key.get_secret_value() or not self.exa_api_key.get_secret_value():
            raise ValueError("Set ANTHROPIC_API_KEY and EXA_API_KEY in .env before starting")
