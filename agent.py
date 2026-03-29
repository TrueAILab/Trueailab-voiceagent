"""
TrueAILab — Voice Agent Demo
Talks to prospects, understands their use case, collects their details,
and sends name/email/phone to n8n webhook.

Run: python agent.py --mode none
"""

import os
import asyncio
import io
import json
import traceback

import cv2
import httpx
import sounddevice as sd
import PIL.Image
import argparse

from dotenv import load_dotenv
from google import genai
from google.genai import types
from google.genai.types import Type

load_dotenv(".env.local")

DTYPE = "int16"
CHANNELS = 1
SEND_SAMPLE_RATE = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE = 1024

MODEL = os.environ.get(
    "GEMINI_MODEL",
    "gemini-3.1-flash-live-preview",
)
DEFAULT_MODE = "none"

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
                    required=["name"],
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
Introduce yourself warmly.
"Hi! This is Jacqueline from TrueAILab. We help businesses automate their phone calls
using AI voice agents. How are you doing today?"

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
- When you have collected ALL FOUR pieces (name, email, phone_number, usecase),
  call save_customer_info ONCE with all four fields, then say the closing line.
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


async def send_to_webhook(data: dict):
    """POST contact data to n8n webhook."""
    try:
        async with httpx.AsyncClient() as http:
            response = await http.post(WEBHOOK_URL, json=data, timeout=5.0)
            print(f"\n[Webhook] Sent: {json.dumps(data, indent=2)}")
            print(f"[Webhook] Response: {response.status_code}")
    except Exception as e:
        print(f"\n[Webhook] Error: {e}")


class AudioLoop:
    def __init__(self, video_mode=DEFAULT_MODE):
        self.video_mode = video_mode
        self.audio_in_queue = None
        self.out_queue = None
        self.session = None
        self.audio_stream = None
        self.output_stream = None
        self.customer_data = {}
        self.webhook_sent = False

    async def send_text(self):
        while True:
            text = await asyncio.to_thread(input, "message > ")
            if text.lower() == "q":
                break
            if self.session is not None:
                await self.session.send_client_content(
                    turns=types.Content(
                        role="user",
                        parts=[types.Part(text=text or ".")],
                    ),
                    turn_complete=True,
                )

    def _get_frame(self, cap):
        ret, frame = cap.read()
        if not ret:
            return None
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        img = PIL.Image.fromarray(frame_rgb)
        img.thumbnail([1024, 1024])
        image_io = io.BytesIO()
        img.save(image_io, format="jpeg")
        image_io.seek(0)
        return {"mime_type": "image/jpeg", "data": image_io.read()}

    async def get_frames(self):
        cap = await asyncio.to_thread(cv2.VideoCapture, 0)
        while True:
            frame = await asyncio.to_thread(self._get_frame, cap)
            if frame is None:
                break
            await asyncio.sleep(1.0)
            if self.out_queue is not None:
                await self.out_queue.put(frame)
        cap.release()

    async def send_realtime(self):
        while True:
            if self.out_queue is not None:
                msg = await self.out_queue.get()
                if self.session is not None:
                    blob = types.Blob(mime_type=msg["mime_type"], data=msg["data"])
                    if msg["mime_type"].startswith("audio/"):
                        await self.session.send_realtime_input(audio=blob)
                    else:
                        await self.session.send_realtime_input(video=blob)

    async def listen_audio(self):
        self.audio_stream = sd.RawInputStream(
            samplerate=SEND_SAMPLE_RATE,
            blocksize=CHUNK_SIZE,
            channels=CHANNELS,
            dtype=DTYPE,
        )
        await asyncio.to_thread(self.audio_stream.start)
        while True:
            data, overflowed = await asyncio.to_thread(self.audio_stream.read, CHUNK_SIZE)
            if overflowed and __debug__:
                continue
            if self.out_queue is not None:
                await self.out_queue.put({"data": bytes(data), "mime_type": "audio/pcm"})

    async def receive_audio(self):
        while True:
            if self.session is not None:
                turn = self.session.receive()
                async for response in turn:
                    # Audio → play
                    if data := response.data:
                        self.audio_in_queue.put_nowait(data)
                        continue

                    # Text → print
                    if text := response.text:
                        print(text, end="", flush=True)

                    # Tool call → send webhook once
                    if response.tool_call:
                        for fc in response.tool_call.function_calls:
                            if fc.name == "save_customer_info" and not self.webhook_sent:
                                self.customer_data.update(fc.args)
                                self.webhook_sent = True
                                print(f"\n[Lead captured] {self.customer_data}")
                                await send_to_webhook(self.customer_data)
                                await self.session.send_tool_response(
                                    function_responses=[
                                        types.FunctionResponse(
                                            name=fc.name,
                                            id=fc.id,
                                            response={"result": "Saved successfully"},
                                        )
                                    ]
                                )

                # Clear audio queue on interruption
                while not self.audio_in_queue.empty():
                    self.audio_in_queue.get_nowait()

    async def play_audio(self):
        self.output_stream = sd.RawOutputStream(
            samplerate=RECEIVE_SAMPLE_RATE,
            blocksize=CHUNK_SIZE,
            channels=CHANNELS,
            dtype=DTYPE,
        )
        await asyncio.to_thread(self.output_stream.start)
        while True:
            if self.audio_in_queue is not None:
                bytestream = await self.audio_in_queue.get()
                await asyncio.to_thread(self.output_stream.write, bytestream)

    async def run(self):
        try:
            async with (
                client.aio.live.connect(model=MODEL, config=CONFIG) as session,
                asyncio.TaskGroup() as tg,
            ):
                self.session = session
                self.audio_in_queue = asyncio.Queue()
                self.out_queue = asyncio.Queue(maxsize=5)

                send_text_task = tg.create_task(self.send_text())
                tg.create_task(self.send_realtime())
                tg.create_task(self.listen_audio())
                if self.video_mode == "camera":
                    tg.create_task(self.get_frames())
                elif self.video_mode == "screen":
                    tg.create_task(self.get_screen())

                tg.create_task(self.receive_audio())
                tg.create_task(self.play_audio())

                await send_text_task
                raise asyncio.CancelledError("User requested exit")

        except asyncio.CancelledError:
            pass
        except ExceptionGroup as EG:
            traceback.print_exception(EG)
        finally:
            if self.audio_stream is not None:
                self.audio_stream.close()
            if self.output_stream is not None:
                self.output_stream.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        type=str,
        default=DEFAULT_MODE,
        help="pixels to stream from",
        choices=["camera", "screen", "none"],
    )
    args = parser.parse_args()
    main = AudioLoop(video_mode=args.mode)
    asyncio.run(main.run())
