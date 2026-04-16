import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

if load_dotenv:
    backend_env = Path(__file__).resolve().parent / ".env"
    if backend_env.exists():
        load_dotenv(dotenv_path=backend_env)
    load_dotenv()


@dataclass
class Settings:
    sarvam_api_key: str = os.getenv("SARVAM_API_KEY", "")
    chat_model: str = os.getenv("SARVAM_CHAT_MODEL", "sarvam-m")
    translate_target: str = os.getenv("SARVAM_DEFAULT_TARGET_LANG", "hi-IN")
    upload_dir: str = os.getenv("UPLOAD_DIR", "storage/uploads")
    max_chunk_chars: int = int(os.getenv("MAX_CHUNK_CHARS", "500"))


settings = Settings()
