import asyncio
from aioble import Service, Characteristic, CharacteristicFlags, Advertisement
import pyaudio
import wave
import os
import datetime
import threading
import queue
import logging

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('audio_server.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# BLE Service and Characteristic UUIDs
SERVICE_UUID = "12345678-1234-5678-1234-56789abcdef0"
CHARACTERISTIC_UUID = "abcdef01-1234-5678-1234-56789abcdef0"

# Audio settings
CHUNK = 1024
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
        self.p = pyaudio.PyAudio()
        self.recording_thread = None

    def start_recording(self):
        if not self.is_recording:
            self.is_recording = True
            self.recording_thread = threading.Thread(target=self._record_continuously)
            self.recording_thread.start()
            logger.info("Started recording")

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

                logger.info(f"Recording: {filename}")
                frames = []

                for _ in range(0, int(RATE / CHUNK * RECORD_SECONDS)):
                    if not self.is_recording:
                        break
                    data = stream.read(CHUNK)
                    frames.append(data)

                stream.stop_stream()
                stream.close()

                wf = wave.open(filename, 'wb')
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(self.p.get_sample_size(FORMAT))
                wf.setframerate(RATE)
                wf.writeframes(b''.join(frames))
                wf.close()

                file_queue.put(filename)
                logger.info(f"Saved: {filename}")

            except Exception as e:
                logger.error(f"Error during recording: {str(e)}")

    def stop_recording(self):
        if self.is_recording:
            self.is_recording = False
            if self.recording_thread:
                self.recording_thread.join()
            self.p.terminate()
            logger.info("Stopped recording")

async def handle_read(characteristic: Characteristic, **kwargs):
    if not file_queue.empty():
        filename = file_queue.get()
        try:
            with open(filename, 'rb') as f:
                data = f.read()
                logger.info(f"Read file for transfer: {filename}")
                return data
        except Exception as e:
            logger.error(f"Error reading file {filename}: {str(e)}")
    return b''

async def handle_connection(connection, recorder):
    logger.info(f"Device connected: {connection.device}")
    recorder.start_recording()
    
    try:
        await connection.disconnected()
    finally:
        logger.info("Device disconnected")
        recorder.stop_recording()

async def main():
    # Create service
    service = Service(SERVICE_UUID)
    
    # Create characteristic
    char = Characteristic(
        CHARACTERISTIC_UUID,
        CharacteristicFlags.READ | CharacteristicFlags.NOTIFY,
        read_handler=handle_read
    )
    service.add_characteristic(char)

    # Create advertisement
    advertisement = Advertisement()
    advertisement.complete_name = "AudioRecorder"
    advertisement.service_uuids = [SERVICE_UUID]

    # Create recorder instance
    recorder = AudioRecorder()
    
    logger.info("Starting BLE server...")
    
    while True:
        try:
            async with await aioble.advertise(advertisement, [service]) as connection:
                logger.info("BLE server running and advertising...")
                
                # Handle the connection
                await handle_connection(connection, recorder)
                
                # Handle file transfers
                while not file_queue.empty():
                    filename = file_queue.get()
                    try:
                        with open(filename, 'rb') as f:
                            data = f.read()
                            # Send in chunks
                            chunk_size = 512
                            total_chunks = len(data) // chunk_size + (1 if len(data) % chunk_size else 0)
                            
                            logger.info(f"Starting transfer of {filename} ({len(data)} bytes in {total_chunks} chunks)")
                            
                            for i in range(0, len(data), chunk_size):
                                chunk = data[i:i + chunk_size]
                                await char.notify(chunk)
                                await asyncio.sleep(0.01)
                            
                            logger.info(f"Completed transfer of {filename}")
                            
                            # Optionally delete the file after successful transfer
                            os.remove(filename)
                            logger.info(f"Deleted file: {filename}")
                            
                    except Exception as e:
                        logger.error(f"Error transferring file {filename}: {str(e)}")
                
        except Exception as e:
            logger.error(f"Connection error: {str(e)}")
            await asyncio.sleep(1)  # Wait before retrying

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Server shutdown by user")
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
