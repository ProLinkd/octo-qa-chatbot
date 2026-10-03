# octo_qa_chatbot

FastAPI Q&A service following the structure of `../interview-assistant`:
token authentication, chat sessions, conversation history, and Server-Sent
Events (SSE). Its knowledge sources are `howIvyWorksHandbook.ts` and
`trainingvideo.txt`.

## Run locally

```bash
cd octo_qa_chatbot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
env 'auth-token=change-me' 'openai-api-token=your-api-key' \
  'redis-host=127.0.0.1' 'redis-port=6379' DEBUG=true \
  uvicorn app.main:app --host 0.0.0.0 --port 8501 --reload
```

Use Python 3.10 or later. With `DEBUG=true`, interactive API docs are at
http://localhost:8501/docs. The default model matches the reference project's
`gpt-6-luna`; set `interview-assistant-openai-model` to a Responses-compatible model available
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

All routes except `GET /health-check` require `Authorization: Token <auth-token>`.
Keep this shared service token on your backend; do not embed it in a public browser app.

| Endpoint | Purpose |
| --- | --- |
| `POST /qa-sessions` | Create a session |
| `GET /qa-sessions/{session_id}` | Read history and expiry |
| `POST /qa-sessions/{session_id}/chat` | Ask a question; stream the answer |
| `GET /knowledge-base` | Inspect indexed chapter, section, and chunk counts |
| `POST /knowledge-base` | Upload an updated `.ts` handbook and refresh RAG immediately |
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
Video sources also include `video_url`. When a video supports an answer, the
assistant is instructed to add `For more information, watch: <video_url>`.
If it cites a video but omits its link, the service appends the recommendation
to the streamed answer. Repeated citations to the same video share one link.

## Retrieval from the handbook and training videos

1. At startup, read the static `howIvyWorksHandbook` exported array using JSON5.
   TypeScript code is never executed. This loader supports the supplied literal
   array format, not arbitrary TypeScript expressions, imports, or computed values.
2. Extract named published fields and structured email samples. Exclude all
   `Unpublished_*` properties and unknown fields before indexing or model calls.
3. Read `trainingvideo.txt`, splitting scripts by video and chapter, retaining
   video links. Scripts without chapters are indexed as a video overview.
   Split both sources into overlapping chunks and build one in-memory BM25 index.
4. Retrieve the top passages for each question. Short pronoun-based follow-ups
   include the previous user question in retrieval.
5. Send retrieved passages and recent chat history to OpenAI's
   [Responses streaming API](https://developers.openai.com/api/docs/guides/streaming-responses).
   Instructions constrain answers to that evidence. No-match questions return
   a fixed fallback without calling OpenAI.

No embeddings, vector database, web search, or other knowledge
sources are used. Retrieval is lexical: questions should use reference terminology;
synonyms and questions in languages other than the references' English may retrieve
poorly. Model grounding is prompt-based and does not guarantee factual accuracy.
The selected excerpts and recent conversation are sent to OpenAI; `store=False`
disables Responses application storage.

Upload the updated handbook as `multipart/form-data` with the required field `file`:

```bash
curl -X POST http://localhost:8501/knowledge-base \
  -H 'Authorization: Token change-me' \
  -F 'file=@./howIvyWorksHandbook.ts'
```

The response contains `file`, `chapters`, `sections`, and `chunks`, just like
`GET /knowledge-base`. Chapter and section counts describe the handbook; the chunk
count includes both sources. The service validates the UTF-8 `.ts` file and builds the
replacement index before atomically saving it to `HANDBOOK_PATH`. The uploaded
filename does not change that destination. Subsequent searches and chat turns use
the updated content without restarting or configuring the API again. Training
video content remains in the index after handbook uploads. Existing
sessions and history are preserved; replies already streaming keep their original
excerpts. Missing files or invalid content return HTTP 422, and files over the
default 10 MiB limit return HTTP 413. Rejected uploads leave the handbook unchanged.

The handbook file and its parent directory must be writable by the service.
Workers sharing `HANDBOOK_PATH` detect changes on their next retrieval request.
Multiple instances need a shared filesystem for handbook updates; Redis shares
sessions only. Use a persistent directory volume for `HANDBOOK_PATH` to retain
uploads when replacing containers. Concurrent valid uploads use the last saved
version. You can also atomically replace the file directly to refresh retrieval.
`HANDBOOK_PATH` can point to another `.ts` file with the same export and schema.
Malformed or missing handbook data causes startup to fail.

`TRAINING_VIDEO_PATH` defaults to the project's `trainingvideo.txt`. It must be a
UTF-8 file with `Video N link: https://...` and `Script Video N:` blocks, optionally
containing `Chapter N` headings followed by a title and script text. Missing or
invalid training data also causes startup to fail. Workers detect changes to either
source on the next request. To update video scripts, replace this file directly;
the upload endpoint continues to accept `.ts` handbook files only.

## Configuration

Shared settings use `os.getenv` with the same environment keys as
`interview-assistant`. Use `env 'key=value' command` locally for keys containing
hyphens; Bash `export` does not accept those names. Q&A-specific settings keep
the uppercase names listed below. Restart the service after changing environment
variables.

| Setting | Default |
| --- | --- |
| `auth-token` | Required for protected endpoints |
| `openai-api-token` | Required for model-backed answers |
| `interview-assistant-openai-model` | `gpt-6-luna` |
| `DEBUG` | `false` |
| `openai-timeout-seconds` | `300` |
| `HANDBOOK_PATH` | Project's `howIvyWorksHandbook.ts` |
| `TRAINING_VIDEO_PATH` | Project's `trainingvideo.txt` |
| `HANDBOOK_MAX_UPLOAD_BYTES` | `10485760` (10 MiB) |
| `RAG_TOP_K` | `6` |
| `RAG_CHUNK_CHARS` | `2400` plus chapter/section heading |
| `session-ttl-seconds` | `864000` (10 days) |
| `MAX_SESSIONS` | `1000` (in-memory backend only) |
| `redis-host` | Empty; set to enable Redis |
| `redis-port` | `6379` |
| `redis-db` | `0` |
| `redis-pass` | Empty |
| `redis-ssl` | `false` |
| `WEB_CONCURRENCY` | `1`; multiple workers supported with Redis |
| `HISTORY_MAX_MESSAGES` | `20` recent messages sent to the model |
| `MAX_OUTPUT_TOKENS` | `4096` |

Set `redis-host` to save sessions and full chat history in Redis. Configure the
connection with `redis-port`, `redis-db`, `redis-pass`, and `redis-ssl`; set
`session-ttl-seconds` to control expiry.

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

Without `redis-host`, the service uses memory and loses sessions on restart. That
mode requires one worker and one instance. With Redis, set `WEB_CONCURRENCY` when
using `exec.sh` to run multiple workers.
Authentication uses one shared token, so callers with that token and a session ID
can read that session. There is no per-user ownership layer.

## Docker

```bash
docker build -t octo_qa_chatbot .
docker run --rm -p 8501:8501 \
  -e 'auth-token=change-me' \
  -e 'openai-api-token=your-api-key' \
  -e 'redis-host=your-redis-host' \
  -e 'redis-port=6379' \
  -e 'redis-db=0' \
  -e 'redis-pass=your-redis-password' \
  -e 'redis-ssl=false' \
  octo_qa_chatbot
```

Use a `redis-host` reachable from the container; `127.0.0.1` refers to the container itself.

Gunicorn serves directly on port 8501. If adding a reverse proxy, disable response
buffering and set a timeout long enough for streamed answers.
