import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(override=False)

MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

_CLIENT = None


def get_client() -> OpenAI:
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set (.env or exported).")

    base_url = os.getenv("OPENAI_BASE_URL")
    if base_url:
        _CLIENT = OpenAI(api_key=api_key, base_url=base_url)
    else:
        _CLIENT = OpenAI(api_key=api_key)
    return _CLIENT
