import os
from config import AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY

class TranscriptionService:
    def __init__(self, use_aws=True):
        """
        Initialize the transcription service.
        :param use_aws: Whether to use AWS Transcribe (True) or Whisper (False).
        """
        self.use_aws = use_aws

        if use_aws:
            import boto3
            self.transcribe_client = boto3.client(
                "transcribe",
                region_name="us-west-1",  # Change to your AWS region
                aws_access_key_id=AWS_ACCESS_KEY_ID,
                aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
            )
        else:
            import whisper
            self.model = whisper.load_model("base")

    async def transcribe(self, audio_buffer):
        """
        Transcribe audio using either AWS or Whisper.
        :param audio_buffer: Raw PCM audio data as bytes.
        :return: Transcription result (string).
        """
        if self.use_aws:
            return await self.transcribe_with_aws(audio_buffer)
        else:
            return self.transcribe_with_whisper(audio_buffer)

    async def transcribe_with_aws(self, audio_buffer):
        """Transcribe audio using AWS Transcribe."""
        response = self.transcribe_client.start_stream_transcription(
            LanguageCode="en-US",
            MediaSampleRateHertz=8000,
            MediaEncoding="pcm",
        )
        async with response["TranscriptionStream"] as stream:
            await stream.input_stream.send(audio_buffer)
            async for event in stream.output_stream:
                if "Transcript" in event:
                    for result in event["Transcript"].get("Results", []):
                        if not result["IsPartial"]:
                            return result["Alternatives"][0]["Transcript"]

    def transcribe_with_whisper(self, audio_buffer):
        """Transcribe audio using Whisper."""
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_file:
            temp_file.write(audio_buffer)
            temp_file.flush()
            temp_file_path = temp_file.name

        try:
            result = self.model.transcribe(temp_file_path)
            return result["text"]
        finally:
            # Clean up the temporary file
            os.unlink(temp_file_path)