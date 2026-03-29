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

MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-native-audio-preview-12-2025")
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
                    "Save the prospect's details. Call this ONCE only "
                    "after you have collected all four: name, email, phone_number, usecase."
                ),
                parameters=types.Schema(
                    type=Type.OBJECT,
                    properties={
                        "name":         types.Schema(type=Type.STRING, description="Prospect's full name"),
                        "email":        types.Schema(type=Type.STRING, description="Prospect's email address"),
                        "phone_number": types.Schema(type=Type.STRING, description="Prospect's phone number"),
                        "usecase":      types.Schema(type=Type.STRING, description="Business problem they described"),
                    },
                    required=["name"],
                ),
            )
        ]
    ),
]

SYSTEM_PROMPT = """
You are Jacqueline, a friendly voice AI sales assistant for TrueAILab.
TrueAILab builds custom voice agents for businesses — AI phone assistants that work 24/7.

YOUR FLOW:
1. Greet warmly: "Hi! This is Jacqueline from TrueAILab. We help businesses automate phone calls with AI. How are you today?"
2. Ask their business and problem: "What kind of business do you run and where are phone calls causing you headaches?"
3. Explain how a voice agent solves THEIR specific problem with a concrete benefit and number.
4. Collect name, email, phone number — one at a time, confirm each back.
5. Once you have all four (name, email, phone_number, usecase) call save_customer_info ONCE.
6. Close: "Our team will reach out within 24 hours with a personalised demo. Thanks!"

RULES:
- Short sentences. One question at a time. This is a phone call.
- Never call save_customer_info more than once.
- Be warm, consultative, never pushy.
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
    try:
        async with httpx.AsyncClient() as http:
            r = await http.post(WEBHOOK_URL, json=data, timeout=5.0)
            print(f"[Webhook] {data} → {r.status_code}")
    except Exception as e:
        print(f"[Webhook error] {e}")


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
    call_done     = asyncio.Event()

    try:
        async with client.aio.live.connect(model=MODEL, config=CONFIG) as session:

            async def from_twilio():
                nonlocal stream_sid
                try:
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
                            break
                except Exception:
                    pass
                finally:
                    call_done.set()

            async def to_twilio():
                nonlocal webhook_sent
                try:
                    while not call_done.is_set():
                        turn = session.receive()
                        async for response in turn:
                            if call_done.is_set():
                                break

                            # Audio → send to caller
                            if response.data:
                                try:
                                    pcm8k   = resample(response.data, 24000, 8000)
                                    ulaw    = pcm16_to_ulaw(pcm8k)
                                    payload = base64.b64encode(ulaw).decode()
                                    await websocket.send_json({
                                        "event":     "media",
                                        "streamSid": stream_sid,
                                        "media":     {"payload": payload},
                                    })
                                except Exception:
                                    call_done.set()
                                    break

                            # Tool call → webhook
                            if response.tool_call:
                                for fc in response.tool_call.function_calls:
                                    if fc.name == "save_customer_info" and not webhook_sent:
                                        customer_data.update(fc.args)
                                        webhook_sent = True
                                        print(f"[Lead] {customer_data}")
                                        await send_to_webhook(customer_data)
                                        await session.send_tool_response(
                                            function_responses=[
                                                types.FunctionResponse(
                                                    name=fc.name,
                                                    id=fc.id,
                                                    response={"result": "Saved"},
                                                )
                                            ]
                                        )
                except Exception:
                    pass

            await asyncio.gather(from_twilio(), to_twilio())

    except Exception:
        traceback.print_exc()
