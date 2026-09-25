import json
import os
from google import genai
from google.genai import types
from dotenv import load_dotenv
load_dotenv()

client=genai.Client()

MODEL_NAME="gemini-embedding-001"
OUTPUT_PATH="data/llm_index_cards.json"
LLM_INDEX_CARDS=[
    "Greeting, small talk, short question, simple arithmetic, basic calculation, definition, rewrite, summary of a short passage, translation, everyday chat.",
    "Complex reasoning, multi-step problem solving, hard math, proofs, coding, debugging, code review, multi-file changes, planning, tool use, long instructions.",
    "Image understanding, image analysis, OCR, chart, diagram, screenshot, document scan, visual question answering, describe this picture, read the text in this image."
]
LLM_NAMES=["openai/gpt-oss-20b", "openai/gpt-oss-120b","qwen/qwen3.8-27b"]


def json_schema_builder() -> None:
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    if not os.path.exists(OUTPUT_PATH):
        embeddings=client.models.embed_content(
            model=MODEL_NAME,
            contents=LLM_INDEX_CARDS,
            config=types.EmbedContentConfig(
                task_type="RETRIEVAL_DOCUMENT",
                title="LLM capability index cards"
            )
        )
        embeddings=embeddings.embeddings
        schema=[]
        for llm_name, emb in zip(LLM_NAMES, embeddings, strict=True):
            schema.append({
                "llm_name":llm_name,
                "embedding":emb.values
            })
        with open(OUTPUT_PATH,"w") as f:
            json.dump(schema, f)
# json_schema_builder()