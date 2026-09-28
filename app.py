from dotenv import load_dotenv
from llm_router.web import serve

load_dotenv()

if __name__ == "__main__":
    serve()
