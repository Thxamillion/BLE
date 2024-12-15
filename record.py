import os
import datetime
import threading
import queue
import pyaudio
import wave
import logging
import time

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('recorder.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Audio settings
CHUNK = 8192
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 44100
RECORD_SECONDS = 30
OUTPUT_DIR = "recordings"

os.makedirs(OUTPUT_DIR, exist_ok=True)
file_queue = queue.Queue()

class AudioRecorder:
    def __init__(self):
        self.is_recording = False
        try:
            self.p = pyaudio.PyAudio()
            # List available audio devices
            for i in range(self.p.get_device_count()):
                device_info = self.p.get_device_info_by_index(i)
                logger.info(f"Audio device {i}: {device_info['name']}")
                logger.info(f"Max Input Channels: {device_info['maxInputChannels']}")
        except Exception as e:
            logger.error(f"Failed to initialize PyAudio: {e}")
            raise
        self.recording_thread = None

    def start_recording(self):
        if not self.is_recording:
            logger.info("Starting audio recording")
            try:
                # Test audio device before starting
                test_stream = self.p.open(
                    format=FORMAT,
                    channels=CHANNELS,
                    rate=RATE,
                    input=True,
                    frames_per_buffer=CHUNK,
                    start=False
                )
                test_stream.close()
                logger.info("Audio device test successful")
                
                self.is_recording = True
                self.recording_thread = threading.Thread(target=self._record_continuously)
                self.recording_thread.start()
                logger.info("Recording thread started")
            except Exception as e:
                logger.error(f"Failed to initialize audio recording: {e}")
                raise
        else:
            logger.warning("Recording already in progress")

    def _record_continuously(self):
        while self.is_recording:
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = os.path.join(OUTPUT_DIR, f"audio_{timestamp}.wav")
            
            try:
                stream = self.p.open(format=FORMAT,
                                   channels=CHANNELS,
                                   rate=RATE,
                                   input=True,
                                   frames_per_buffer=CHUNK)

                logger.info(f"Started new recording segment: {filename}")
                frames = []

                for _ in range(0, int(RATE / CHUNK * RECORD_SECONDS)):
                    if not self.is_recording:
                        logger.info("Recording stopped by request")
                        break
                    try:
                        data = stream.read(CHUNK)
                        frames.append(data)
                    except Exception as e:
                        logger.error(f"Error reading audio data: {e}")
                        break

                stream.stop_stream()
                stream.close()

                if frames:  # Only save if we actually recorded something
                    wf = wave.open(filename, 'wb')
                    wf.setnchannels(CHANNELS)
                    wf.setsampwidth(self.p.get_sample_size(FORMAT))
                    wf.setframerate(RATE)
                    wf.writeframes(b''.join(frames))
                    wf.close()

                    file_queue.put(filename)
                    logger.info(f"Successfully saved recording: {filename}")
                    logger.info(f"File size: {os.path.getsize(filename)} bytes")
            except Exception as e:
                logger.error(f"Error during recording: {e}")

    def stop_recording(self):
        if self.is_recording:
            logger.info("Stopping audio recording")
            self.is_recording = False
            if self.recording_thread:
                self.recording_thread.join()
            self.p.terminate()

    def get_next_file(self):
        """Get the next available audio file from the queue"""
        if not file_queue.empty():
            return file_queue.get()
        return None 

def run_recorder():
    logger.info("Starting continuous recording service...")
    recorder = AudioRecorder()
    try:
        recorder.start_recording()
        logger.info("Recording started. Press Ctrl+C to stop.")
        # Keep the main thread alive
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Recording service stopped by user")
    finally:
        recorder.stop_recording()
        logger.info("Recording service stopped")

if __name__ == "__main__":
    run_recorder()