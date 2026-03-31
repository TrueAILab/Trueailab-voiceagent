"""
TrueAILab — Twilio <-> Gemini Live Voice Bridge
Run locally: uvicorn server:app --host 0.0.0.0 --port 8000
Then expose with ngrok: ngrok http 8000
Set Twilio webhook to: https://YOUR-NGROK-URL/incoming-call
"""

import asyncio
import base64
import json
import os
import traceback

import httpx
import numpy as np
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import Response
from google import genai
from google.genai import types
from google.genai.types import Type
from dotenv import load_dotenv

load_dotenv(".env.local")

app = FastAPI()

MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-live-preview")
WEBHOOK_URL = "https://n8n.trueailab.com/webhook/trueailab"

client = genai.Client(
    http_options={"api_version": "v1beta"},
    api_key=os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY"),
)

tools = [
    types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name="save_customer_info",
                description=(
                    "Save the prospect's details. Call this ONCE only, "
                    "after you have collected ALL four pieces of information: "
                    "name, email, phone_number, and usecase. "
                    "Do NOT call this more than once per conversation."
                ),
                parameters=types.Schema(
                    type=Type.OBJECT,
                    properties={
                        "name": types.Schema(
                            type=Type.STRING,
                            description="Prospect's full name",
                        ),
                        "email": types.Schema(
                            type=Type.STRING,
                            description="Prospect's email address",
                        ),
                        "phone_number": types.Schema(
                            type=Type.STRING,
                            description="Prospect's phone number",
                        ),
                        "usecase": types.Schema(
                            type=Type.STRING,
                            description="The business problem or scenario the prospect described",
                        ),
                    },
                    required=["name", "email", "phone_number", "usecase"],
                ),
            )
        ]
    ),
]

SYSTEM_PROMPT = """
You are Jacqueline, a friendly and knowledgeable voice AI sales assistant for TrueAILab.

TrueAILab is a software engineering company that builds custom voice agents for businesses.
Voice agents are AI-powered phone assistants that handle calls 24/7, collect leads, answer
questions, book appointments, and automate repetitive phone tasks.

YOUR CONVERSATION FLOW — follow this order naturally, one step at a time:

STEP 1 — GREET
Keep it very short. Just say:
"Hi, I'm Jacqueline from TrueAILab. We build AI voice agents. How can I help you?"

AUDIBILITY CHECK — if the caller says anything like "can you hear me?", "hello?",
"am I audible?", "are you there?", "can you hear me now?", or any similar check:
Respond immediately: "Yes, I can hear you clearly!"
Then do the short intro again: "I'm Jacqueline from TrueAILab. We build AI voice agents. How can I help you?"

STEP 2 — UNDERSTAND THEIR PROBLEM
Ask about their business and the problem they are trying to solve.
"Tell me — what kind of business do you run, and where are phone calls causing
you the most headache right now?"
Listen fully. Common scenarios:
- Appointment booking (clinics, salons, consultants)
- Lead capture (real estate, insurance, agencies)
- Customer support (e-commerce, SaaS, services)
- Order taking (restaurants, delivery, retail)
- Follow-up calls (sales teams, surveys, reminders)

STEP 3 — VALIDATE WITH A PRODUCTIVITY INSIGHT
Based on what they said, explain specifically how a voice agent solves THEIR problem.
Always give a concrete benefit with a number.
Examples:
- "Clinics using voice agents reduce missed appointments by 40% because
   the agent auto-confirms bookings even at midnight."
- "Real estate agencies capture 3x more leads because the agent answers
   every missed call instantly instead of going to voicemail."
- "E-commerce teams cut support costs by 60% because the agent handles
   order status and returns without a human."
Be honest. If their scenario is not a strong fit for a voice agent, say so.

STEP 4 — COLLECT CONTACT DETAILS
Once they are interested and the problem is clear, say:
"I'd love to get our team to put together a personalised demo for you.
Could I get your name, email, and a phone number where we can reach you?"
Collect name, email, and phone number one by one through natural conversation.
Confirm each detail back to them as they give it.

STEP 5 — SAVE AND CLOSE
Once you have ALL FOUR pieces — name, email, phone number, and their use case —
call save_customer_info ONCE with all four fields.
Then say:
"Perfect! Our team will be in touch within 24 hours with a demo built
specifically for your scenario. Really appreciate your time today, goodbye!"

STRICT RULES:
- Keep sentences short. This is a voice call, not an email.
- Never list multiple questions at once. Ask one thing at a time.
- If they ask what a voice agent is:
  "A voice agent is an AI that answers and makes phone calls exactly like a human —
   it handles hundreds of calls at once, never sleeps, and never misses a lead."
- Be warm, consultative, and confident. Never pushy.
- Collect name, email, and phone number quickly — aim to gather all contact details within 2–3 turns.
- CRITICAL: Once you have ALL FOUR pieces (name, email, phone_number, usecase), you MUST call
  save_customer_info immediately with all four fields. Do NOT skip this step under any circumstance.
  Then say the closing line.
"""

CONFIG = types.LiveConnectConfig(
    response_modalities=["AUDIO"],
    system_instruction=SYSTEM_PROMPT,
    speech_config=types.SpeechConfig(
        voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Zephyr")
        )
    ),
    tools=tools,
)


# ── Audio conversion ──────────────────────────────────────────────────────────
# Twilio → mulaw 8kHz  →  convert to PCM 16kHz  →  Gemini
# Gemini → PCM 24kHz   →  convert to mulaw 8kHz  →  Twilio

def ulaw_to_pcm16(data: bytes) -> bytes:
    ulaw = np.frombuffer(data, dtype=np.uint8).astype(np.int32)
    ulaw = ~ulaw & 0xFF
    sign     = ulaw & 0x80
    exponent = (ulaw >> 4) & 0x07
    mantissa = ulaw & 0x0F
    value    = ((mantissa | 0x10) << (exponent + 1)) - 33
    pcm      = np.where(sign != 0, -value, value).astype(np.int16)
    return pcm.tobytes()


def pcm16_to_ulaw(data: bytes) -> bytes:
    pcm  = np.frombuffer(data, dtype=np.int16).astype(np.int32)
    sign = np.where(pcm < 0, 0x80, 0).astype(np.uint8)
    pcm  = np.abs(pcm) + 132
    pcm  = np.clip(pcm, 0, 32767)
    exp  = np.floor(np.log2(np.maximum(pcm, 1))).astype(np.int32)
    exp  = np.clip(exp - 7, 0, 7)
    mant = ((pcm >> (exp + 3)) & 0x0F).astype(np.uint8)
    ulaw = (~(sign | (exp.astype(np.uint8) << 4) | mant)) & 0xFF
    return ulaw.astype(np.uint8).tobytes()


def resample(data: bytes, from_rate: int, to_rate: int) -> bytes:
    pcm = np.frombuffer(data, dtype=np.int16).astype(np.float32)
    n   = int(len(pcm) * to_rate / from_rate)
    if n == 0:
        return b""
    idx = np.linspace(0, len(pcm) - 1, n)
    return np.interp(idx, np.arange(len(pcm)), pcm).astype(np.int16).tobytes()


async def send_to_webhook(data: dict):
    """POST contact data to n8n webhook."""
    try:
        async with httpx.AsyncClient() as http:
            response = await http.post(WEBHOOK_URL, json=data, timeout=5.0)
            print(f"\n[Webhook] Sent: {json.dumps(data, indent=2)}")
            print(f"[Webhook] Response: {response.status_code}")
    except Exception as e:
        print(f"\n[Webhook] Error: {e}")


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {"status": "TrueAILab voice agent running"}


@app.api_route("/incoming-call", methods=["GET", "POST"])
async def incoming_call(request: Request):
    """Twilio hits this when someone calls your number."""
    host = request.headers.get("host")
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say>Please hold.</Say>
    <Connect>
        <Stream url="wss://{host}/media-stream" />
    </Connect>
</Response>"""
    return Response(content=twiml, media_type="application/xml")


@app.websocket("/media-stream")
async def media_stream(websocket: WebSocket):
    await websocket.accept()
    stream_sid    = None
    customer_data = {}
    webhook_sent  = False

    try:
        async with client.aio.live.connect(model=MODEL, config=CONFIG) as session:

            async def from_twilio():
                nonlocal stream_sid, webhook_sent
                async for raw in websocket.iter_text():
                    msg   = json.loads(raw)
                    event = msg.get("event")

                    if event == "start":
                        stream_sid = msg["start"]["streamSid"]
                        print(f"[Call started] {stream_sid}")

                    elif event == "media":
                        ulaw   = base64.b64decode(msg["media"]["payload"])
                        pcm8k  = ulaw_to_pcm16(ulaw)
                        pcm16k = resample(pcm8k, 8000, 16000)
                        await session.send_realtime_input(
                            audio=types.Blob(mime_type="audio/pcm", data=pcm16k)
                        )

                    elif event == "stop":
                        print("[Call ended]")
                        if customer_data and not webhook_sent:
                            webhook_sent = True
                            print(f"[Fallback lead save on call end] {customer_data}")
                            await send_to_webhook(customer_data)
                        break

            async def to_twilio():
                nonlocal webhook_sent
                try:
                    while True:
                        turn = session.receive()
                        async for response in turn:
                            # Audio → send to caller
                            if getattr(response, "data", None):
                                try:
                                    pcm8k   = resample(response.data, 24000, 8000)
                                    ulaw    = pcm16_to_ulaw(pcm8k)
                                    payload = base64.b64encode(ulaw).decode()
                                    await websocket.send_json({
                                        "event":     "media",
                                        "streamSid": stream_sid,
                                        "media":     {"payload": payload},
                                    })
                                except Exception as e:
                                    print(f"[to_twilio] send error: {e}")

                            if response.text:
                                print("[AI TEXT]:", response.text)

                            # Tool call → save + webhook
                            if response.tool_call:
                                print("[TOOL CALL TRIGGERED]", [fc.name for fc in response.tool_call.function_calls])
                                for fc in response.tool_call.function_calls:
                                    if fc.name == "save_customer_info" and not webhook_sent:
                                        customer_data.update(fc.args)
                                        webhook_sent = True
                                        print(f"\n[Lead captured] {customer_data}")
                                        await send_to_webhook(customer_data)
                                        await session.send_tool_response(
                                            function_responses=[
                                                types.FunctionResponse(
                                                    name=fc.name,
                                                    id=fc.id,
                                                    response={"result": "Saved successfully"},
                                                )
                                            ]
                                        )
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    print(f"[to_twilio] error: {e}")

            # Run to_twilio as a cancellable task — cancel it when the call ends
            # so the Gemini session is properly closed and not left open/leaked.
            to_twilio_task = asyncio.create_task(to_twilio())
            try:
                await from_twilio()
            finally:
                to_twilio_task.cancel()
                await asyncio.gather(to_twilio_task, return_exceptions=True)
                print(f"[Session closed] {stream_sid}")

    except Exception:
        traceback.print_exc()
    finally:
        await websocket.close()
