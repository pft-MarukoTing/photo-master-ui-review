"""The chat page's model calls, run by the worker.

The backend owns the session -- its log, plans and photo states -- and asks
the model only through these jobs: it reads what the model needs (the
analysis, the plan, the recent log), queues it on the chat queue, and waits
for the answer. Nothing here reads or writes the session; a job's params are
everything it gets, and its result is everything it gives back.

Chat jobs have their own queue and their own worker threads (server.py's
check_chat_queue), so a reply is never stuck behind renders that take
minutes.
"""

import os

from engine_result_code import RESULT_INVALID_PARAMETER
from photo_agent import chat_assistant

# Read by both sides, so the backend and the worker agree on it from the
# shared .env.default alone: the job queue's name with ":chat" on the end,
# unless REDIS_CHAT_QUEUE names one.
CHAT_QUEUE = os.getenv("REDIS_CHAT_QUEUE") or (
    os.getenv("REDIS_MSG_QUEUE", "yce_ai:photoAgent:queue").split(",")[0] + ":chat")

# The typed message about one photo: chat_assistant.ask.
STAGE_ASK = "chat_ask"
# One line typed on the upload page: chat_assistant.review_line.
STAGE_REVIEW = "chat_review"
# The first note on a freshly analyzed photo, with 詳細解說 on:
# chat_assistant.write_opening.
STAGE_OPENING = "chat_opening"
# The note under a finished version, with 詳細解說 on:
# chat_assistant.write_render_note. The backend sends the two images as
# small data URLs, so this job needs no S3 either.
STAGE_NOTE = "chat_note"
STAGES = (STAGE_ASK, STAGE_REVIEW, STAGE_OPENING, STAGE_NOTE)


def run(params):
    """The model's answer for one chat job's params, or raises."""
    stage = params.get("stage")
    if stage == STAGE_ASK:
        return chat_assistant.ask(
            params.get("analysis"), params["plan"], params["status"], params["history"],
            debug=bool(params.get("debug")), screen=params.get("screen"))
    if stage == STAGE_REVIEW:
        return chat_assistant.review_line(
            params["line"], params.get("prompt", ""), params.get("history", []), params.get("people", []))
    if stage == STAGE_OPENING:
        return chat_assistant.write_opening(params.get("analysis"), params.get("wishes") or "")
    if stage == STAGE_NOTE:
        return chat_assistant.write_render_note(
            params.get("analysis"), params["plan"], before=params.get("before"), after=params.get("after"),
            previous_plan=params.get("previous_plan"), earlier_notes=params.get("earlier_notes") or ())
    raise RuntimeError(f"unknown chat stage: {stage}", RESULT_INVALID_PARAMETER)
