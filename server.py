import asyncio
from bleak import BleakServer
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.service import BleakGATTService
import pyaudio
import wave
import os
import datetime
import threading
import queue

# BLE Service and Characteristic UUIDs
SERVICE_UUID = "12345678-1234-5678-1234-56789abcdef0"
CHARACTERISTIC_UUID = "abcdef01-1234-5678-1234-56789abcdef0"

# Audio settings
CHUNK = 1024
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 44100  # Better quality for transcription
RECORD_SECONDS = 30  # Record in 30-second segments
OUTPUT_DIR = "recordings"

os.makedirs(OUTPUT_DIR, exist_ok=True)
file_queue = queue.Queue()

class AudioRecorder:
    def __init__(self):
        self.is_recording = False
        self.p = pyaudio.PyAudio()

    def start_recording(self):
        self.is_recording = True
        threading.Thread(target=self._record_continuously).start()

    def _record_continuously(self):
        while self.is_recording:
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = os.path.join(OUTPUT_DIR, f"audio_{timestamp}.wav")
            
            # Open audio stream
            stream = self.p.open(format=FORMAT,
                               channels=CHANNELS,
                               rate=RATE,
                               input=True,
                               frames_per_buffer=CHUNK)

            print(f"Recording: {filename}")
            frames = []

            # Record audio
            for _ in range(0, int(RATE / CHUNK * RECORD_SECONDS)):
                if not self.is_recording:
                    break
                data = stream.read(CHUNK)
                frames.append(data)

            # Stop and close the stream
            stream.stop_stream()
            stream.close()

            # Save the audio file
            wf = wave.open(filename, 'wb')
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(self.p.get_sample_size(FORMAT))
            wf.setframerate(RATE)
            wf.writeframes(b''.join(frames))
            wf.close()

            # Add file to queue for transmission
            file_queue.put(filename)
            print(f"Saved: {filename}")

    def stop_recording(self):
        self.is_recording = False
        self.p.terminate()

async def send_file_data(server, characteristic):
    while True:
        try:
            if not file_queue.empty():
                filename = file_queue.get()
                with open(filename, 'rb') as f:
                    file_data = f.read()
                    
                    # Send file size first (4 bytes)
                    size_bytes = len(file_data).to_bytes(4, 'big')
                    await server.notify(None, characteristic, size_bytes)
                    await asyncio.sleep(0.1)  # Small delay

                    # Send file data in chunks
                    chunk_size = 512  # Larger chunks since we're not streaming
                    for i in range(0, len(file_data), chunk_size):
                        chunk = file_data[i:i + chunk_size]
                        await server.notify(None, characteristic, chunk)
                        await asyncio.sleep(0.01)  # Small delay between chunks

                    print(f"Sent file: {filename}")
                    
                    # Optionally, delete the file after sending
                    # os.remove(filename)
            
            await asyncio.sleep(1)
        except Exception as e:
            print(f"Send error: {e}")

async def main():
    server = BleakServer()
    
    # Create service and characteristic
    service = BleakGATTService(SERVICE_UUID)
    characteristic = BleakGATTCharacteristic(
        CHARACTERISTIC_UUID,
        ["read", "notify"],
        description="Audio File Transfer"
    )
    service.add_characteristic(characteristic)
    server.add_service(service)

    # Start audio recording
    recorder = AudioRecorder()
    recorder.start_recording()

    # Start BLE server
    await server.start()
    print("BLE server running...")

    # Start sending files
    await send_file_data(server, characteristic)

    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        recorder.stop_recording()
        await server.stop()

if __name__ == "__main__":
    asyncio.run(main())