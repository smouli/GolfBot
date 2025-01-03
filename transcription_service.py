import json
import config
from typing import AsyncGenerator
import boto3
from websockets import connect
import asyncio



class AWSTranscribeWebSocket:
    def __init__(self, region: str, access_key: str = config.AWS_ACCESS_KEY_ID, secret_key: str = config.AWS_SECRET_ACCESS_KEY):
        self.region = region
        self.endpoint = f"wss://transcribestreaming.{region}.amazonaws.com:8443/stream-transcription-websocket"
        self.ws = None  # Placeholder for WebSocket connection

        # Initialize the boto3 client
        self.transcribe_client = self._initialize_boto3_client(region, access_key, secret_key)

    def _initialize_boto3_client(self, region: str, access_key: str = None, secret_key: str = None):
        """Initialize the boto3 client for AWS Transcribe."""
        try:
            if access_key and secret_key:
                # Use provided credentials
                client = boto3.client(
                    "transcribe",
                    region_name=region,
                    aws_access_key_id=access_key,
                    aws_secret_access_key=secret_key,
                )
            else:
                # Use default credentials (e.g., environment variables, ~/.aws/credentials)
                client = boto3.client("transcribe", region_name=region)

            print(f"Initialized AWS Transcribe client in region: {region}")
            return client
        except Exception as e:
            print(f"Error initializing AWS Transcribe client: {e}")
            raise

    async def connect(self):
        """Connect to AWS Transcribe WebSocket."""
        try:
            params = "?language-code=en-US&media-encoding=pcm&sample-rate=16000"
            url = self.endpoint + params

            print(f"Connecting to AWS Transcribe WebSocket at {url}")
            self.ws = await connect(url)
            if self.ws:
                print("Connected to AWS Transcribe WebSocket successfully")
            else:
                print("Failed to establish WebSocket connection")
        except Exception as e:
            print(f"Error connecting to AWS Transcribe WebSocket: {e}")
            raise

    async def send_audio(self, ws, audio_chunk: bytes):
        """Send audio chunks to AWS Transcribe."""
        try:
            if ws is None:
                raise ValueError("WebSocket connection is not established")
            await ws.send(audio_chunk)
        except Exception as e:
            print(f"Error sending audio chunk to AWS Transcribe: {e}")
            raise

    async def receive_transcription(self, ws) -> AsyncGenerator[str, None]:
        """Receive transcription results from AWS Transcribe."""
        if ws is None:
            print("WebSocket connection is not established for receiving transcription")
            return

        try:
            async for message in ws:
                response = json.loads(message)
                if "Transcript" in response:
                    for result in response["Transcript"]["Results"]:
                        if not result["IsPartial"]:
                            transcription = result["Alternatives"][0]["Transcript"]
                            yield transcription
        except Exception as e:
            print(f"Error receiving transcription: {e}")
            raise