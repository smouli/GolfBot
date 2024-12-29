import numpy as np

class SilenceDetector:
    def __init__(self, threshold=500, sample_rate=8000):
        """
        Initialize the SilenceDetector.
        :param threshold: Amplitude threshold to consider audio as silence.
        :param sample_rate: Sample rate of the audio (default: 8 kHz for Twilio).
        """
        self.threshold = threshold
        self.sample_rate = sample_rate

    def is_silence(self, audio_chunk):
        """
        Analyze audio chunk to detect silence.
        :param audio_chunk: Raw PCM audio data as bytes.
        :return: True if silence is detected, otherwise False.
        """
        # Convert raw PCM audio to NumPy array
        audio_array = np.frombuffer(audio_chunk, dtype=np.int16)

        # Calculate the average amplitude
        avg_amplitude = np.mean(np.abs(audio_array))
        print(f"Average Amplitude: {avg_amplitude}")

        # Return True if amplitude is below the threshold
        return avg_amplitude < self.threshold