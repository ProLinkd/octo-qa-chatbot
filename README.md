# octo_qa_chatbot

FastAPI Q&A service following the structure of `../interview-assistant`:
token authentication, chat sessions, conversation history, and Server-Sent
Events (SSE). Its only knowledge source is `howIvyWorksHandbook.ts`.

## Run locally

```bash
cd octo_qa_chatbot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export AUTH_TOKEN='change-me'
export OPENAI_API_KEY='your-api-key'
export REDIS_HOST='127.0.0.1'
export REDIS_PORT='6379'
DEBUG=true uvicorn app.main:app --host 0.0.0.0 --port 8501 --reload
```

Use Python 3.10 or later. With `DEBUG=true`, interactive API docs are at
http://localhost:8501/docs. The default model matches the reference project's
`gpt-6-luna`; set `QA_OPENAI_MODEL` to a Responses-compatible model available
to your account.

## Ask a question

Create a session (no body required):

```bash
curl -X POST http://localhost:8501/qa-sessions \
  -H 'Authorization: Token change-me'
```

Copy `session_id` from the response:

```bash
curl -N http://localhost:8501/qa-sessions/SESSION_ID/chat \
  -H 'Authorization: Token change-me' \
  -H 'Content-Type: application/json' \
  -d '{"message":"How do I reschedule an interview in Ivy?"}'

curl http://localhost:8501/qa-sessions/SESSION_ID \
  -H 'Authorization: Token change-me'
```

All routes except `GET /health-check` require `Authorization: Token <AUTH_TOKEN>`.
Keep this shared service token on your backend; do not embed it in a public browser app.

| Endpoint | Purpose |
| --- | --- |
| `POST /qa-sessions` | Create a session |
| `GET /qa-sessions/{session_id}` | Read history and expiry |
| `POST /qa-sessions/{session_id}/chat` | Ask a question; stream the answer |
| `GET /knowledge-base` | Inspect indexed chapter, section, and chunk counts |
| `GET /knowledge-base/search?query=interview` | Inspect retrieved published excerpts |
| `GET /health-check` | Check service health |

The chat response is `text/event-stream`. Each event is a JSON `data:` line
followed by a blank line:

```text
data: {"type":"sources","sources":[...]}

data: {"type":"delta","content":"To reschedule..."}

data: {"type":"done","content":"<complete answer>","sources":[...]}

```

On generation failure, `{"type":"error","detail":"..."}` replaces `done`.
Discard partial output on error. Failed or disconnected incomplete turns are not
saved. Concurrent chat requests for the same session return HTTP 409.
Each source includes `source_id`, `file`, `chapter_id`, `section_id`, and `title`.
Answers are instructed to cite `[source_id]`; the sources array contains all
retrieved passages, which may include passages the answer did not use.

## Retrieval from the TypeScript file

1. At startup, read the static `howIvyWorksHandbook` exported array using JSON5.
   TypeScript code is never executed. This loader supports the supplied literal
   array format, not arbitrary TypeScript expressions, imports, or computed values.
2. Extract named published fields and structured email samples. Exclude all
   `Unpublished_*` properties and unknown fields before indexing or model calls.
3. Split sections into overlapping chunks and build an in-memory BM25 index.
4. Retrieve the top passages for each question. Short pronoun-based follow-ups
   include the previous user question in retrieval.
5. Send retrieved passages and recent chat history to OpenAI's
   [Responses streaming API](https://developers.openai.com/api/docs/guides/streaming-responses).
   Instructions constrain answers to that evidence. No-match questions return
   a fixed fallback without calling OpenAI.

No embeddings, vector database, file uploads, web search, or other knowledge
sources are used. Retrieval is lexical: questions should use handbook terminology;
synonyms and questions in languages other than the handbook's English may retrieve
poorly. Model grounding is prompt-based and does not guarantee factual accuracy.
The selected excerpts and recent conversation are sent to OpenAI; `store=False`
disables Responses application storage.

Edit the existing `.ts` file and restart the service to rebuild the index.
`HANDBOOK_PATH` can point to another `.ts` file with the same export and schema.
Malformed or missing handbook data causes startup to fail.

## Configuration

Configure the service using environment variables. Standard uppercase
environment variables are supported. `auth-token`, `openai-api-token`, and `qa-openai-model`
are also accepted for compatibility with the reference service.

| Setting | Default |
| --- | --- |
| `AUTH_TOKEN` | Required for protected endpoints |
| `OPENAI_API_KEY` | Required for model-backed answers |
| `QA_OPENAI_MODEL` | `gpt-6-luna` |
| `DEBUG` | `false` |
| `OPENAI_TIMEOUT_SECONDS` | `300` |
| `HANDBOOK_PATH` | Project's `howIvyWorksHandbook.ts` |
| `RAG_TOP_K` | `6` |
| `RAG_CHUNK_CHARS` | `2400` plus chapter/section heading |
| `SESSION_TTL_SECONDS` | `864000` (10 days) |
| `MAX_SESSIONS` | `1000` (in-memory backend only) |
| `REDIS_HOST` | Empty; set to enable Redis |
| `REDIS_PORT` | `6379` |
| `REDIS_DB` | `0` |
| `REDIS_PASSWORD` | Empty |
| `REDIS_SSL` | `false` |
| `WEB_CONCURRENCY` | `1`; multiple workers supported with Redis |
| `HISTORY_MAX_MESSAGES` | `20` recent messages sent to the model |
| `MAX_OUTPUT_TOKENS` | `4096` |

Set `REDIS_HOST` to save sessions and full chat history in Redis. The service also
accepts the reference project's `redis-host`, `redis-port`, `redis-db`, `redis-pass`,
`redis-ssl`, and `session-ttl-seconds` environment names.

Each session is stored as JSON under `qa-session:{SESSION_ID}` (including the braces).
It contains `session_id`, `created_at`, `expires_at`, and `messages`. Assistant
messages include the completed answer and retrieved source references. Successful
turns are saved before the `done` event, and refresh the session TTL (10 days by
default). Active turns reserve enough TTL to finish. Failed or incomplete model
answers are not saved. A Redis lock serializes requests for the same session across
workers and instances; expired lock owners cannot overwrite newer results.

Sessions survive application restarts and are shared by workers using the same
Redis database. For persistence across Redis restarts, configure Redis AOF or RDB
persistence and a persistent data volume. If configured Redis is unavailable, startup
fails or requests return an error; the service does not silently switch to memory.

Without `REDIS_HOST`, the service uses memory and loses sessions on restart. That
mode requires one worker and one instance. With Redis, set `WEB_CONCURRENCY` when
using `exec.sh` to run multiple workers.
Authentication uses one shared token, so callers with that token and a session ID
can read that session. There is no per-user ownership layer.

## Docker

```bash
docker build -t octo_qa_chatbot .
docker run --rm -p 8501:8501 -e AUTH_TOKEN -e OPENAI_API_KEY \
  -e REDIS_HOST -e REDIS_PORT -e REDIS_DB -e REDIS_PASSWORD -e REDIS_SSL \
  octo_qa_chatbot
```

Use a `REDIS_HOST` reachable from the container; `127.0.0.1` refers to the container itself.

Gunicorn serves directly on port 8501. If adding a reverse proxy, disable response
buffering and set a timeout long enough for streamed answers.
