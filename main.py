from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
load_dotenv()

user_query = input("User query : ")
user_query_length=len(user_query)

# Defining a simple query classifier based on char length
query_class="Small"
if user_query_length > 2000:
    query_class="Large"
elif user_query_length > 500:
    query_class="Medium"

# Defining the policy for LLM routing

route_to="allam-2-7b"

if query_class == "Large":
    route_to="openai/gpt-oss-120b"
elif query_class == "Medium":
    route_to="qwen/qwen3.8-27b"

# Implementing LLM calls for user query
# No multi turn conversation possible for sake of simplicity

llm = init_chat_model(
    model=route_to,
    model_provider="groq"
)

print(f"The query has been classified to be a {query_class} class query and it will be routed shortly!")
print(f"The query is being routed to : {route_to}")

response=llm.invoke(user_query)
print(response.content)