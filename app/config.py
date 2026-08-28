import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

GATEWAY_URL = "https://ai-gateway.vercel.sh/v1/chat/completions"
MODEL = os.getenv("MODEL", "openai/gpt-5.6-sol")
API_KEY = (os.getenv("AI_GATEWAY_API_KEY") or "").strip()
DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()
STATIC_DIR = Path(__file__).parent / "static"
