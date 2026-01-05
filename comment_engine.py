import logging
from openai import OpenAI
import os
from dotenv import load_dotenv
from typing import Optional

load_dotenv()
key = os.getenv('OPENAI_API_KEY')

logger = logging.getLogger(__name__)

def build_prompt(post_text: str, system_prompt) -> list:
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Пост: {post_text}\nКомментарий:"}
    ]

def generate_comment(post_text: str, system_prompt) -> str:
    messages = build_prompt(post_text, system_prompt)
    try:
        if not key:
            logger.error("[ERROR] OpenAI configuration error: OPENAI_API_KEY is not set")
            return ""

        client = OpenAI(api_key=key)
        logger.debug(f"Отправка запроса к OpenAI для генерации комментария. Пост: {post_text[:100]}...")
        
        response = client.chat.completions.create(
            model='gpt-4o',
            messages=messages,
            temperature=0.7,
            max_tokens=200
        )
        
        # Проверяем наличие ответа
        if not response or not response.choices:
            logger.error("[ERROR] OpenAI вернул пустой ответ (нет choices)")
            return ""
        
        if not response.choices[0].message:
            logger.error("[ERROR] OpenAI вернул ответ без message")
            return ""
        
        comment_text = response.choices[0].message.content
        if comment_text is None:
            logger.error("[ERROR] OpenAI вернул ответ с content=None")
            return ""
        
        comment_text = comment_text.strip()
        if not comment_text:
            logger.warning("OpenAI вернул пустой комментарий (после strip)")
            return ""
        
        logger.debug(f"Успешно сгенерирован комментарий: {comment_text[:50]}...")
        return comment_text
        
    except RuntimeError as e:
        logger.error(f"[ERROR] OpenAI configuration error: {e}")
        return ""
    except Exception as e:
        error_msg = str(e)
        # Логируем более детально
        if "401" in error_msg or "invalid_api_key" in error_msg.lower():
            logger.error(f"[ERROR] OpenAI API key is invalid or expired. Please check OPENAI_API_KEY in .env file")
        elif "429" in error_msg or "rate_limit" in error_msg.lower():
            logger.error(f"[ERROR] OpenAI rate limit exceeded: {e}")
        elif "500" in error_msg or "502" in error_msg or "503" in error_msg:
            logger.error(f"[ERROR] OpenAI server error: {e}")
        else:
            logger.error(f"[ERROR] OpenAI error: {e}", exc_info=True)
        return ""
