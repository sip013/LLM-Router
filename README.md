# LLM Router

A local web app that chooses a model for each question and shows the reason on screen.

Every message passes three checks before a model is called. Eligibility drops models that cannot take the request. When more than one text model remains, TypeSafe Jev (`typesafe/jev-1.13` through the OpenRouter Decisions API) picks a task and a preferred model. A fixed ranking policy then orders the eligible models by quality and expected speed. Groq writes the reply. OpenRouter is used only for Jev.

The design behind those checks is in [PRODUCTION_ARCHITECTURE.md](PRODUCTION_ARCHITECTURE.md).

## Run it

You need Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
copy .env.example .env
uv run python app.py
```

On macOS or Linux, use `cp .env.example .env` instead of `copy`. Then open http://127.0.0.1:8765.

Put these keys in `.env`. The file stays on your machine and is listed in `.gitignore`.

| Variable | Used for |
| --- | --- |
| `GROQ_API_KEY` | Replies, and the second-opinion check |
| `OPENROUTER_API_KEY` | Jev only |

## What the page does

- A start screen, then a conversation. New thread can be undone for a few seconds.
- Your message stays in a bubble. The reply appears as it is written, then becomes the formatted reading text (code blocks and tables) once the model finishes. Formatting still happens on the server.
- A chip above each reply names the model, the task, and Jev's confidence. Open it for the decision: eligibility, analysis, ranking, retries, and how the candidates compared.
- Attach a PNG, JPEG, GIF, or WebP image, or reuse the last one. Paste and drag-and-drop work. "Double-check answer" asks a different model to review the reply.
- The mark in the header is the catalog. While a request is out, a light travels toward each model. When the reply arrives, it settles on the model that answered.

The server listens on `0.0.0.0:8765`, so another device on the same subnet can open this computer's address on that port. The Host header has to be localhost, this computer's name, or a private address. Windows Firewall may still block other devices until port 8765 is allowed for the local subnet. Anyone who can reach the page sends prompts with the keys in your `.env`.

## Models

| Name | Id | When it fits |
| --- | --- | --- |
| GPT-OSS 20B | `openai/gpt-oss-20b` | Short questions, definitions, rewrites |
| GPT-OSS 120B | `openai/gpt-oss-120b` | Code, proofs, and multi-step planning |
| Qwen 3.8 27B | `qwen/qwen3.8-27b` | An attached image, OCR, or a chart |

All three are called through Groq. Quality and expected latency live in `llm_router/registry.py` and are starting estimates until a published quality table replaces them.

## Request path

1. **Eligibility.** Residency, modality, context length, the latency ceiling, and the denylist. A model that fails one of these is out, with a reason.
2. **Analysis.** One eligible model is used as-is. An image goes to the vision model. Otherwise Jev chooses among the eligible ids. A low-confidence choice falls back to the ranking policy, and a short follow-up can stay with the previous model.
3. **Ranking.** Score is task quality, minus a cost term and an expected-latency term. The top model answers. The next model is the fallback.
4. **Execution.** One retry on the same model, then one fallback. A rate limit skips that provider for the rest of the turn.
5. **Checks.** Optional JSON schema validation, and an optional verifier that is not the model that wrote the answer.

Each turn is appended to `data/traces.jsonl`. That file, and uploaded images under `data/uploads/`, are local and are not committed.

## Tests

```bash
uv run python -m unittest discover -s tests
```

## Layout

```
app.py                         Web entry point
llm_router/pipeline.py         Eligibility, Jev, ranking
llm_router/execute.py          Retries, fallback, verification, traces
llm_router/web.py              Local server, sessions, and the reply payload
web/                           Page, styles, and the 3D mark
tests/test_routing.py          Routing and browser-boundary tests
```

## Third-party assets

The page ships its own fonts and its own copy of Three.js, so the browser never loads a remote script.

- [Three.js](https://github.com/mrdoob/three.js) r170, MIT, in `web/vendor/three.module.min.js`
- Inter, Newsreader, and JetBrains Mono variable fonts from [Fontsource](https://fontsource.org/), SIL Open Font License, in `web/fonts/`
