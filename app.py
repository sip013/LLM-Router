import json
import os
import numpy as np
from dotenv import load_dotenv
from google import genai
from google.genai import types
from langchain.chat_models import init_chat_model
from index_card_embeddings_generator import MODEL_NAME, LLM_INDEX_CARDS, json_schema_builder, LLM_NAMES
load_dotenv()
json_schema_builder()

user_query = input("User query : ")
user_query_length=len(user_query)
client=genai.Client()

# Generating the query embedding
def query_embedding_generator(user_query):
    embedding=client.models.embed_content(
        model=MODEL_NAME,
        contents=[user_query],
        config=types.EmbedContentConfig(
            task_type="RETRIEVAL_QUERY"
        )
    )
    return embedding.embeddings[0].values

# Defining the query classifier based on query length
def semantic_query_classifier(user_query, llm_index_card_embeddings):
    user_query_embedding=query_embedding_generator(user_query)
    cosine_similarities=[]
    for embedding in llm_index_card_embeddings:
        cosine_similarity=np.dot(embedding["embedding"], user_query_embedding)
        cosine_similarities.append(cosine_similarity)
    
    index=cosine_similarities.index(max(cosine_similarities))
    print(f"Your query will be routed to {LLM_NAMES[index]}")
    return index

# Implementing LLM calls for user query
# No multi turn conversation kept for sake of simplicity
def llm_call(user_query, llm_index_card_embeddings):
    llm = init_chat_model(
        model=LLM_NAMES[semantic_query_classifier(user_query, llm_index_card_embeddings)],
        model_provider="groq"
    )
    response=llm.invoke(user_query)
    return response.content
llm_index_card_embeddings=json.load(open("data/llm_index_cards.json","r"))
text_response=llm_call(user_query, llm_index_card_embeddings)
print(f"Here is the response from the LLM: {text_response}")