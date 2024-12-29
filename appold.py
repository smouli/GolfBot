from flask import Flask, request
from twilio.twiml.voice_response import VoiceResponse
import openai
import requests
import io
import wave
import config 

app = Flask(__name__)

# OpenAI API Key
openai.api_key = config.OPENAI_API_KEY

@app.route("/incoming-call", methods=["POST"])
def handle_call():
    """Handles incoming calls and starts the recording process."""
    response = VoiceResponse()
    print("came 1")
    # Prompt user and record their input
    response.say("Welcome to the AI-powered assistant. Please say something after the beep.")
    print("came 2")
    response.record(max_length=30, action="/process-recording", transcribe=False)
    print("came 3")
    response.say("Thank you. Processing your request now.")
    print("came 4")
    return str(response)

@app.route("/process-recording", methods=["POST"])
def process_recording():
    """Processes the audio recording and responds."""
    print("came 5")
    recording_url = request.form.get("RecordingUrl")
    print("came 6")

    if not recording_url:
        return "No recording URL found", 400
    
    print("came 7")
    # Fetch the audio file from Twilio
    print(recording_url)
    
    audio_response = requests.get(
        recording_url,
        auth=(config.TWILIO_AUTH_SID, config.TWILIO_AUTH_TOKEN)  # Replace with Twilio credentials
    )
    print("came 8")
    print(audio_response.content)
    if audio_response.status_code != 200:
        print("came 9")
        return "Failed to fetch the recording", 400

    # Transcribe the audio using OpenAI Whisper
    transcription = transcribe_audio_with_whisper(audio_response.content)
    print("came 10")
    # Generate a response using GPT-4
    bot_response = generate_chatbot_response(transcription)
    print("came 11")
    # Convert the response into speech (Optional: Use ElevenLabs or similar)
    audio_url = convert_to_speech(bot_response)
    print("came 12")
    # Respond to the caller
    response = VoiceResponse()
    if audio_url:
        print("came 13")
        response.play(audio_url)
    else:
        print("came 14")
        response.say(bot_response)  # Fallback to Twilio TTS
    return str(response)

def transcribe_audio_with_whisper(audio_data):
    """Transcribes audio using OpenAI Whisper."""
    with io.BytesIO() as wav_file:
        with wave.open(wav_file, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)  # 16-bit audio
            wav.setframerate(8000)  # Twilio default sample rate
            wav.writeframes(audio_data)

        wav_file.seek(0)
        transcription = openai.Audio.transcribe("whisper-1", file=wav_file)
        return transcription.get("text", "")

def generate_chatbot_response(user_input):
    """Generates a response using GPT-4."""
    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": user_input},
        ]
    )
    return response['choices'][0]['message']['content']

def convert_to_speech(text):
    """Converts text to speech using ElevenLabs or similar TTS services."""
    headers = {
        "Authorization": "Bearer your-elevenlabs-api-key",
        "Content-Type": "application/json"
    }
    payload = {"text": text, "voice": "Rachel"}
    tts_response = requests.post("https://api.elevenlabs.io/v1/text-to-speech", headers=headers, json=payload)
    if tts_response.status_code == 200:
        return tts_response.json().get("audio_url", "")
    return None

if __name__ == "__main__":
    app.run(debug=True, port=8000)