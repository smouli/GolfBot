from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import PlainTextResponse
from twilio.twiml.voice_response import VoiceResponse
from openai import OpenAI
import asyncio
from typing import AsyncGenerator
from transcription_service import AWSTranscribeWebSocket
import json
import base64
import config
import uvicorn

#openai.api_key = config.OPENAI_API_KEY
client = OpenAI(
    api_key=config.OPENAI_API_KEY,  # This is the default and can be omitted
)
app = FastAPI()

# "wss://954f-2600-1700-5fa7-46d0-4db8-2fe9-2b9-2ae.ngrok-free.app/audio-stream"
@app.post("/incoming-call")
async def incoming_call(request: Request):
    """Twilio incoming-call webhook to handle new calls."""
    # Generate the TwiML response
    response = VoiceResponse()

    # Start a Media Stream and specify the WebSocket endpoint
    response.start().stream(
        url=config.TWILIO_WEB_SOCKET_URL + "/audio-stream",  # Replace with your WebSocket URL
        track="inbound"
    )

    response.say("Hello, what can i do for you")
    response.pause(length=30)  # Pause for 30 seconds
    # Return the TwiML response as plain text
    return PlainTextResponse(str(response), media_type="text/xml")

@app.websocket("/audio-stream")
async def audio_stream(websocket: WebSocket):
    """WebSocket endpoint to handle Twilio Media Streams."""
    await websocket.accept()
    print("WebSocket connection established with Twilio")

    aws_transcriber = AWSTranscribeWebSocket(region="us-east-1")
    aws_ws = None

    async def audio_generator():
        """Generate audio chunks from Twilio Media Stream."""
        try:
            while True:
                message = await websocket.receive_text()
                event = json.loads(message)

                if event.get("event") == "media":
                    # Decode Base64 payload
                    audio_chunk = base64.b64decode(event["media"]["payload"])
                    yield audio_chunk

                elif event.get("event") == "stop":
                    print("Twilio Media Stream stopped")
                    break
        except Exception as e:
            print(f"Error in audio generator: {e}")

    try:
        # Connect to AWS Transcribe WebSocket
        aws_ws = await aws_transcriber.connect()
        if aws_ws is None:
            print("AWS WebSocket connection failed. Terminating audio stream.")
            return

        async def send_audio_to_aws():
            """Send audio chunks to AWS Transcribe."""
            async for audio_chunk in audio_generator():
                await aws_transcriber.send_audio(aws_ws, audio_chunk)

        async def receive_transcriptions_from_aws():
            """Receive transcriptions from AWS Transcribe."""
            async for transcription in aws_transcriber.receive_transcription(aws_ws):
                print(f"Received Transcription: {transcription}")

        # Run both tasks concurrently
        await asyncio.gather(send_audio_to_aws(), receive_transcriptions_from_aws())

    except Exception as e:
        print(f"Error during transcription: {e}")
    finally:
        # Cleanup and close connections
        if aws_ws:
            await aws_ws.close()
        print("WebSocket connection closed")
    
def generate_chatbot_response(user_input):
    """Generates a conversational response using GPT-4."""

    response = OpenAI.ChatCompletion.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": user_input},
        ]
    )
    return response['choices'][0]['message']['content']

def convert_to_speech(text):
    """Convert text to speech using ElevenLabs."""
    headers = {
        "Authorization": "Bearer your-elevenlabs-api-key",
        "Content-Type": "application/json"
    }
    payload = {"text": text, "voice": "Rachel"}
    response = requests.post("https://api.elevenlabs.io/v1/text-to-speech", headers=headers, json=payload)
    if response.status_code == 200:
        return response.json().get("audio_url", "")
    return None

if __name__ == "__main__":
      uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)