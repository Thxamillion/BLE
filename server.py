import asyncio
import os
import datetime
import threading
import queue
import pyaudio
import wave
from dbus_next.aio import MessageBus
from dbus_next.service import ServiceInterface, method, dbus_property, signal, Variant
from dbus_next.constants import BusType, PropertyAccess

# Audio settings
CHUNK = 4096
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 16000
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

class GATTService(ServiceInterface):
    def __init__(self):
        super().__init__('org.bluez.GattService1')
        self._uuid = "12345678-1234-5678-1234-56789abcdef0"
        self._primary = True

    @dbus_property(access=PropertyAccess.READ)
    def UUID(self) -> 's':
        return self._uuid

    @dbus_property(access=PropertyAccess.READ)
    def Primary(self) -> 'b':
        return self._primary

    @dbus_property(access=PropertyAccess.READ)
    def Characteristics(self) -> 'ao':
        return ['/org/bluez/example/characteristic0']

class GATTCharacteristic(ServiceInterface):
    def __init__(self):
        super().__init__('org.bluez.GattCharacteristic1')
        self._uuid = "abcdef01-1234-5678-1234-56789abcdef0"
        self._flags = ['read', 'notify']
        self._service = '/org/bluez/example/service0'
        self._value = []

    @dbus_property(access=PropertyAccess.READ)
    def UUID(self) -> 's':
        return self._uuid

    @dbus_property(access=PropertyAccess.READ)
    def Service(self) -> 'o':
        return self._service

    @dbus_property(access=PropertyAccess.READ)
    def Flags(self) -> 'as':
        return self._flags

    @method()
    def ReadValue(self, options: 'a{sv}') -> 'ay':
        if not file_queue.empty():
            filename = file_queue.get()
            try:
                with open(filename, 'rb') as f:
                    data = f.read(512)  # Read in chunks of 512 bytes
                    self._value = list(data)
                    return self._value
            except FileNotFoundError:
                print(f"File not found: {filename}")
        return []

    @method()
    def StartNotify(self):
        print("Notifications started")

    @method()
    def StopNotify(self):
        print("Notifications stopped")

async def setup_bluez():
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()

    # Register the service
    service = GATTService()
    bus.export('/org/bluez/example/service0', service)

    # Register the characteristic
    characteristic = GATTCharacteristic()
    bus.export('/org/bluez/example/characteristic0', characteristic)

    return bus

async def main():
    # Start the audio recorder
    recorder = AudioRecorder()
    recorder.start_recording()

    # Setup D-Bus and BlueZ
    bus = await setup_bluez()
    
    print("BLE GATT server running...")
    
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        recorder.stop_recording()
        print("Server stopped")

if __name__ == '__main__':
    asyncio.run(main())
