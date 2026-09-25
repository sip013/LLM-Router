import json
import os
from google import genai
from google.genai import types
from dotenv import load_dotenv
load_dotenv()

client=genai.Client()

MODEL_NAME="gemini-embedding-001"
OUTPUT_PATH="data/llm_index_cards.json"
SIMPLE_MODEL="openai/gpt-oss-20b"
REASONING_MODEL="openai/gpt-oss-120b"
VISION_MODEL="qwen/qwen3.8-27b"
SCORE_MARGIN=0.02
LLM_ROUTES=[
    {
        "task":"chat",
        "title":"everyday chat",
        "card":"Greeting, small talk, short question, basic arithmetic, definition, rewrite, summary of a short passage, translation, everyday chat.",
    },
    {
        "task":"reasoning",
        "title":"reasoning and code",
        "card":"Explain an algorithm and write the Python. Coding, debugging, code review, mathematical proof, multi-step problem solving, planning, repository changes, long instructions.",
    },
]


def cache_is_current(saved) -> bool:
    if not isinstance(saved, list) or len(saved) != len(LLM_ROUTES):
        return False
    for record, route in zip(saved, LLM_ROUTES):
        if record.get("task") != route["task"]:
            return False
        if record.get("card") != route["card"]:
            return False
        if record.get("title") != route["title"]:
            return False
        if not isinstance(record.get("embedding"), list):
            return False
    return True


def load_index_cards() -> list:
    with open(OUTPUT_PATH, encoding="utf-8") as f:
        return json.load(f)


def json_schema_builder() -> None:
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    if os.path.exists(OUTPUT_PATH) and cache_is_current(load_index_cards()):
        return

    schema=[]
    for route in LLM_ROUTES:
        response=client.models.embed_content(
            model=MODEL_NAME,
            contents=route["card"],
            config=types.EmbedContentConfig(
                task_type="RETRIEVAL_DOCUMENT",
                title=route["title"],
            ),
        )
        schema.append({
            "task":route["task"],
            "title":route["title"],
            "card":route["card"],
            "embedding":response.embeddings[0].values,
        })
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(schema, f)
