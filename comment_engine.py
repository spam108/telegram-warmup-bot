
from openai import OpenAI
import os
from dotenv import load_dotenv
from typing import Optional

load_dotenv()
key = os.getenv('OPENAI_API_KEY')

_client: Optional[OpenAI] = None


def build_prompt(post_text: str, system_prompt) -> list:
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Пост: {post_text}\nКомментарий:"}
    ]


def _get_client() -> OpenAI:
    global _client

    if _client is None:
        if not key:
            raise RuntimeError("OPENAI_API_KEY is not set")

        _client = OpenAI(api_key=key)

    return _client


def generate_comment(post_text: str, system_prompt) -> str:
    messages = build_prompt(post_text, system_prompt)
    try:
        client = _get_client()
        response = client.responses.create(model='gpt-4o', input=messages)
        return response.output_text
    except RuntimeError as e:
        print(f"[ERROR] OpenAI configuration error: {e}")
        return ""
    except Exception as e:
        print(f"[ERROR] OpenAI error: {e}")
        return ""
