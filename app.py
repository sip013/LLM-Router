import base64
import json
import mimetypes
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage
from llm_router.execute import run_turn
from llm_router.pipeline import NoEligibleModel
from llm_router.registry import BY_ID
from llm_router.trace import TRACE_PATH, label_last

load_dotenv()


def clean_image_path(raw_path):
    path = raw_path.strip()
    if len(path) >= 2 and path[0] == path[-1] and path[0] in "\"'":
        path = path[1:-1].strip()
    return path


def resolve_image_path(raw_path, last_image_path):
    path = clean_image_path(raw_path)
    if path.lower() == "same":
        if last_image_path and Path(last_image_path).is_file():
            print(f"Reusing the last image: {last_image_path}")
            return last_image_path
        print("There is no previous image to reuse. The query will be routed as text.")
        return ""
    if not path:
        return ""
    if not Path(path).is_file():
        print("The image path was not found. The query will be routed as text.")
        return ""
    return path


def image_message(user_query, image_path):
    path = Path(image_path)
    mime_type, _ = mimetypes.guess_type(path.name)
    if mime_type is None:
        mime_type = "application/octet-stream"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return HumanMessage(
        content=[
            {"type": "text", "text": user_query},
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime_type};base64,{encoded}"},
            },
        ]
    )


def conversation_messages(history, model_id, image_path):
    messages = []
    last_index = len(history) - 1
    accepts_image = "image" in BY_ID[model_id].modalities
    for index, turn in enumerate(history):
        if turn["role"] == "assistant":
            messages.append(AIMessage(content=turn["text"]))
            continue
        if index == last_index and image_path and accepts_image:
            print(f"The image will be sent with the query to {model_id}.")
            messages.append(image_message(turn["text"], image_path))
            continue
        messages.append(HumanMessage(content=turn["text"]))
    return messages


def print_decision(decision):
    print(f"Analysis: {decision['analysis_source']} ({decision['task_type']})")
    if decision["rejected"]:
        for item in decision["rejected"]:
            print(f"  ruled out {item['model_id']}: {item['reason']} ({item['detail']})")
    for row in decision["ranked"]:
        marker = "selected" if row["model_id"] == decision["selected"] else "backup"
        print(f"  {marker} {row['model_id']}: {row['score']}")
    jev = decision.get("jev") or {}
    if jev.get("error"):
        print(f"Jev unavailable: {jev['error']}")
    elif jev:
        print(
            f"Jev route={jev.get('route')} "
            f"confidence={jev.get('route_confidence')} "
            f"task={jev.get('task_type')} "
            f"task_confidence={jev.get('task_confidence')}"
        )


def load_response_schema():
    path = Path("data/response_schema.json")
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def llm_call(history, image_path, previous_model_id, require_verification):
    result = run_turn(
        history,
        image_path,
        previous_model_id,
        conversation_messages,
        require_verification=require_verification,
        response_schema=load_response_schema(),
        trace_path=TRACE_PATH,
    )
    print_decision(result["decision"])
    if result["validation"] not in {"ok", "not_required"}:
        print(f"Output check: {result['validation']}")
    if result["verification"] != "not_run":
        print(f"Verification: {result['verification']}")
    return result["text"], result["model_id"]


def main():
    history = []
    last_image_path = ""
    last_model_id = None
    require_verification = False
    print("Commands: empty query or exit to stop, reset to clear this conversation.")
    print("label pass or label fail marks the last reply. verify on or verify off arms a second model.")
    print("Image path: Enter to skip, same to reuse the last image.")
    while True:
        user_query = input("User query : ").strip()
        if not user_query or user_query.lower() in {"exit", "quit"}:
            break
        if user_query.lower() == "reset":
            history = []
            last_image_path = ""
            last_model_id = None
            print("The conversation and the last image were cleared.")
            continue
        if user_query.lower() in {"label pass", "label fail"}:
            label = user_query.lower().split()[-1]
            print("Labeled the last reply." if label_last(label) else "There is no completed reply to label.")
            continue
        if user_query.lower() == "verify on":
            require_verification = True
            print("A second model will check each reply.")
            continue
        if user_query.lower() == "verify off":
            require_verification = False
            print("Second-model checks are off, unless Jev asks for one.")
            continue
        image_path = resolve_image_path(
            input("Image path (Enter to skip, same to reuse): "),
            last_image_path,
        )
        history.append({"role": "user", "text": user_query})
        try:
            text_response, model_id = llm_call(history, image_path, last_model_id, require_verification)
        except NoEligibleModel as exc:
            history.pop()
            print("No model is allowed to serve this request.")
            for item in exc.rejected:
                print(f"  {item['model_id']}: {item['reason']} ({item['detail']})")
            continue
        except Exception as exc:
            history.pop()
            print(f"The model call failed: {exc}")
            continue
        if image_path:
            last_image_path = image_path
        last_model_id = model_id
        history.append({"role": "assistant", "text": text_response})
        print(f"Here is the response from the LLM: {text_response}")


if __name__ == "__main__":
    main()
