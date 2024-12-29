from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import PlainTextResponse
from twilio.twiml.voice_response import VoiceResponse
from openai import OpenAI
from TranscriptionService import TranscriptionService

from SilenceDetector import SilenceDetector
import WhisperAPIConnector
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
        url="wss://76c0-2600-1700-5fa7-46d0-4db8-2fe9-2b9-2ae.ngrok-free.app/audio-stream",  # Replace with your WebSocket URL
        track="both"
    )

    response.say("Hello, what can i do for you")
    response.pause(length=30)  # Pause for 30 seconds
    # Return the TwiML response as plain text
    return PlainTextResponse(str(response), media_type="text/xml")

@app.websocket("/audio-stream")
async def audio_stream(websocket: WebSocket):
    """WebSocket endpoint for Twilio Media Stream."""

    await websocket.accept()
    print("WebSocket connection established")

    audio_buffer = b""  # Buffer for storing raw audio data
    silence_detector = SilenceDetector(threshold=500, sample_rate=8000)
    transcription_service = TranscriptionService(use_aws=True)  # Toggle between AWS and Whisper

    try:
        while True:
            message = await websocket.receive_text()
            event = json.loads(message)

            if event.get("event") == "media":
                # Decode the audio payload
                audio_chunk = base64.b64decode(event["media"]["payload"])
                audio_buffer += audio_chunk

                async def process_audio_buffer(buffer):
                    """Transcribe and handle audio buffer."""
                    transcription = await transcription_service.transcribe(buffer)
                    print(f"Transcription: {transcription}")
                    # Handle the transcription result (e.g., send back to Twilio, log, etc.)
                
                # Use the SilenceDetector to check for silence
                if silence_detector.is_silence(audio_chunk):
                    print("Silence detected")
                    await process_audio_buffer(audio_buffer)
                    audio_buffer = b""  # Clear buffer after processing
                else:
                    print("Speech detected")
                    # Continue buffering or reset any silence timers

            elif event.get("event") == "stop":
                print("Media Stream stopped")
                # Process remaining buffer if the stream ends
                if audio_buffer:
                    await process_audio_buffer(audio_buffer)
                break
    except Exception as e:
        print(f"WebSocket error: {e}")
    finally:
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