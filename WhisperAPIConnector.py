import whisper
import tempfile
import subprocess

def transcribe_audio_with_whisper(audio_buffer):
    """
    Transcribe audio using OpenAI Whisper.
    :param audio_buffer: Raw PCM audio data as bytes.
    :return: Transcribed text.
    """
    try:
        # Step 1: Create a temporary file for the raw audio
        with tempfile.NamedTemporaryFile(delete=True, suffix=".raw") as temp_raw:
            temp_raw.write(audio_buffer)
            temp_raw.flush()

            # Step 2: Create another temporary file for the WAV audio
            with tempfile.NamedTemporaryFile(delete=True, suffix=".wav") as temp_wav:
                # Use ffmpeg to convert raw PCM to WAV (16-bit PCM, 16 kHz)
                ffmpeg_command = [
                    "ffmpeg",
                    "-f", "s16le",  # Input format: signed 16-bit little-endian PCM
                    "-ar", "8000",  # Input sample rate (Twilio streams at 8 kHz)
                    "-ac", "1",     # Input channels: mono
                    "-i", temp_raw.name,  # Input file
                    "-ar", "16000",       # Output sample rate: 16 kHz
                    "-ac", "1",           # Output channels: mono
                    temp_wav.name         # Output file
                ]
                subprocess.run(ffmpeg_command, check=True)

                # Step 3: Transcribe the WAV audio using Whisper
                model = whisper.load_model("base")
                result = model.transcribe(temp_wav.name)
                return result["text"]
    except subprocess.CalledProcessError as e:
        print(f"FFmpeg error: {e}")
        raise
    except Exception as e:
        print(f"Error in transcription: {e}")
        raise