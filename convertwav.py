import wave
import numpy as np

def raw_to_wav(input_raw_file, output_wav_file, sample_rate=44100, num_channels=1, sample_width=2):
    """
    Converts a raw audio file to WAV format.

    Parameters:
        input_raw_file (str): Path to the input raw file.
        output_wav_file (str): Path to the output WAV file.
        sample_rate (int): Sampling rate of the audio (default: 44100).
        num_channels (int): Number of audio channels (1 for mono, 2 for stereo).
        sample_width (int): Sample width in bytes (2 for 16-bit audio).

    """
    # Read the raw audio file
    with open(input_raw_file, 'rb') as raw_file:
        raw_data = raw_file.read()
    
    # Convert raw data to numpy array
    audio_data = np.frombuffer(raw_data, dtype=np.int16)
    
    # Create the WAV file
    with wave.open(output_wav_file, 'wb') as wav_file:
        wav_file.setnchannels(num_channels)
        wav_file.setsampwidth(sample_width)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(audio_data.tobytes())

    print(f"Converted {input_raw_file} to {output_wav_file}.")

# Example usage
input_raw_file = "/Users/sanatmouli/Desktop/input.raw"
output_wav_file = "/Users/sanatmouli/downloads/output.wav"
sample_rate = 8000
num_channels = 1
sample_width = 1
raw_to_wav(input_raw_file, output_wav_file, )
