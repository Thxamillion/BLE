import asyncio
from aiobleserver import BLEServer, Service, Characteristic, GATT_READABLE
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
RATE = 44100
RECORD_SECONDS = 30
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
            
            stream = self.p.open(format=FORMAT,
                               channels=CHANNELS,
                               rate=RATE,
                               input=True,
                               frames_per_buffer=CHUNK)

            print(f"Recording: {filename}")
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
            print(f"Saved: {filename}")

    def stop_recording(self):
        self.is_recording = False
        self.p.terminate()

async def handle_read(connection, characteristic):
    if not file_queue.empty():
        filename = file_queue.get()
        with open(filename, 'rb') as f:
            data = f.read()
            # Optionally delete the file after reading
            # os.remove(filename)
            return data
    return b''

async def main():
    # Create BLE server
    ble = BLEServer("AudioRecorder")
    
    # Create service
    service = Service(SERVICE_UUID)
    
    # Create characteristic
    char = Characteristic(
        CHARACTERISTIC_UUID,
        GATT_READABLE,
        handle_read
    )
    service.add_characteristic(char)
    ble.add_service(service)

    # Start the recorder
    recorder = AudioRecorder()
    recorder.start_recording()

    # Start the BLE server
    await ble.start()
    print("BLE server running...")

    try:
        while True:
            if not file_queue.empty():
                filename = file_queue.get()
                with open(filename, 'rb') as f:
                    data = f.read()
                    # Send in chunks
                    chunk_size = 512
                    for i in range(0, len(data), chunk_size):
                        chunk = data[i:i + chunk_size]
                        await ble.notify_all(SERVICE_UUID, CHARACTERISTIC_UUID, chunk)
                        await asyncio.sleep(0.01)
                    print(f"Sent file: {filename}")
                    # Optionally delete the file after sending
                    # os.remove(filename)
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        recorder.stop_recording()
        await ble.stop()

if __name__ == "__main__":
    asyncio.run(main())