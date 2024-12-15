import bluetooth
import threading
import pyaudio
import wave
import os
import datetime
import logging
import json

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Audio settings
CHUNK = 1024
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 44100
RECORD_SECONDS = 30
OUTPUT_DIR = "recordings"

os.makedirs(OUTPUT_DIR, exist_ok=True)

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

                logger.info(f"Saved: {filename}")
                
                # Send the file
                if self.client_sock:
                    self.send_file(filename)

            except Exception as e:
                logger.error(f"Error during recording: {str(e)}")

    def stop_recording(self):
        if self.is_recording:
            self.is_recording = False
            if self.recording_thread:
                self.recording_thread.join()
            self.p.terminate()
            logger.info("Stopped recording")

    def send_file(self, filename):
        try:
            # First send file metadata
            filesize = os.path.getsize(filename)
            metadata = {
                "filename": os.path.basename(filename),
                "filesize": filesize
            }
            metadata_json = json.dumps(metadata).encode()
            
            # Send metadata length first (4 bytes)
            self.client_sock.send(len(metadata_json).to_bytes(4, 'big'))
            # Send metadata
            self.client_sock.send(metadata_json)

            # Send file data
            with open(filename, 'rb') as f:
                data = f.read(1024)
                while data:
                    self.client_sock.send(data)
                    data = f.read(1024)
            
            logger.info(f"Sent file: {filename}")
            
            # Optionally delete the file after sending
            # os.remove(filename)
            
        except Exception as e:
            logger.error(f"Error sending file {filename}: {str(e)}")

def start_server():
    server_sock = bluetooth.BluetoothSocket(bluetooth.RFCOMM)
    server_sock.bind(("", bluetooth.PORT_ANY))
    server_sock.listen(1)

    port = server_sock.getsockname()[1]
    uuid = "94f39d29-7d6d-437d-973b-fba39e49d4ee"

    bluetooth.advertise_service(
        server_sock, "AudioRecorderService",
        service_id=uuid,
        service_classes=[uuid, bluetooth.SERIAL_PORT_CLASS],
        profiles=[bluetooth.SERIAL_PORT_PROFILE]
    )

    logger.info(f"Waiting for connection on RFCOMM channel {port}")
    recorder = AudioRecorder()

    while True:
        try:
            client_sock, client_info = server_sock.accept()
            logger.info(f"Accepted connection from {client_info}")
            
            recorder.client_sock = client_sock
            recorder.start_recording()

            # Keep connection alive and monitor for disconnection
            while True:
                try:
                    # Simple connection check
                    client_sock.send(b'\x00')
                    threading.Event().wait(1)
                except:
                    logger.info("Client disconnected")
                    recorder.stop_recording()
                    client_sock.close()
                    break

        except Exception as e:
            logger.error(f"Error in main loop: {str(e)}")
            if 'client_sock' in locals():
                client_sock.close()
            recorder.stop_recording()

    server_sock.close()

if __name__ == "__main__":
    try:
        start_server()
    except KeyboardInterrupt:
        logger.info("Server shutdown by user")
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
