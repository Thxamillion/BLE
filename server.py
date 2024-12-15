import asyncio
import os
import datetime
import threading
import queue
import pyaudio
import wave
import logging
from dbus_next.aio import MessageBus
from dbus_next.service import ServiceInterface, method, dbus_property, signal, Variant
from dbus_next.constants import BusType, PropertyAccess

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('ble_server.log'),
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
        self.p = pyaudio.PyAudio()
        self.recording_thread = None

    def start_recording(self):
        if not self.is_recording:
            logger.info("Starting audio recording")
            self.is_recording = True
            self.recording_thread = threading.Thread(target=self._record_continuously)
            self.recording_thread.start()

    def _record_continuously(self):
        while self.is_recording:
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = os.path.join(OUTPUT_DIR, f"audio_{timestamp}.wav")
            
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

            if frames:  # Only save if we actually recorded something
                wf = wave.open(filename, 'wb')
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(self.p.get_sample_size(FORMAT))
                wf.setframerate(RATE)
                wf.writeframes(b''.join(frames))
                wf.close()

                file_queue.put(filename)
                logger.info(f"Saved: {filename}")

    def stop_recording(self):
        if self.is_recording:
            logger.info("Stopping audio recording")
            self.is_recording = False
            if self.recording_thread:
                self.recording_thread.join()
            self.p.terminate()

class GATTApplication(ServiceInterface):
    def __init__(self):
        super().__init__('org.bluez.GattApplication1')
        self._services = ['/org/bluez/example/service0']

    @method()
    def GetManagedObjects(self) -> 'a{oa{sa{sv}}}':
        return {
            '/org/bluez/example/service0': {
                'org.bluez.GattService1': {
                    'UUID': Variant('s', "12345678-1234-5678-1234-56789abcdef0"),
                    'Primary': Variant('b', True),
                    'Characteristics': Variant('ao', ['/org/bluez/example/characteristic0'])
                }
            },
            '/org/bluez/example/characteristic0': {
                'org.bluez.GattCharacteristic1': {
                    'UUID': Variant('s', "abcdef01-1234-5678-1234-56789abcdef0"),
                    'Service': Variant('o', '/org/bluez/example/service0'),
                    'Flags': Variant('as', ['read', 'notify'])
                }
            }
        }

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
    def __init__(self, recorder: AudioRecorder):
        super().__init__('org.bluez.GattCharacteristic1')
        self._uuid = "abcdef01-1234-5678-1234-56789abcdef0"
        self._flags = ['read', 'notify']
        self._service = '/org/bluez/example/service0'
        self._value = []
        self.recorder = recorder
        self._clients = set()

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
                    logger.info(f"Sending chunk of size {len(data)} bytes")
                    
                    # Delete the file after successful read
                    try:
                        os.remove(filename)
                        logger.info(f"Deleted file after transfer: {filename}")
                    except OSError as e:
                        logger.error(f"Error deleting file {filename}: {e}")
                    
                    return self._value
            except FileNotFoundError:
                logger.error(f"File not found: {filename}")
        return []

    @method()
    def StartNotify(self):
        sender = self.get_sender()
        logger.info(f"Client connected: {sender}")
        self._clients.add(sender)
        if len(self._clients) == 1:  # First client connected
            self.recorder.start_recording()

    @method()
    def StopNotify(self):
        sender = self.get_sender()
        logger.info(f"Client disconnected: {sender}")
        self._clients.discard(sender)
        if not self._clients:  # No more clients connected
            self.recorder.stop_recording()

async def setup_bluez():
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    
    # Debug: Check adapter status
    adapter_path = '/org/bluez/hci0'
    try:
        adapter = bus.get_proxy_object('org.bluez', adapter_path).get_interface('org.bluez.Adapter1')
        properties = bus.get_proxy_object('org.bluez', adapter_path).get_interface('org.freedesktop.DBus.Properties')
        
        # Enable adapter and make it discoverable
        await properties.call_set('org.bluez.Adapter1', 'Powered', Variant('b', True))
        await properties.call_set('org.bluez.Adapter1', 'Discoverable', Variant('b', True))
        await properties.call_set('org.bluez.Adapter1', 'Pairable', Variant('b', True))
        
        logger.info("Bluetooth adapter configured successfully")
        
        # Get and log adapter properties
        powered = await properties.call_get('org.bluez.Adapter1', 'Powered')
        discoverable = await properties.call_get('org.bluez.Adapter1', 'Discoverable')
        logger.info(f"Adapter powered: {powered.value}")
        logger.info(f"Adapter discoverable: {discoverable.value}")
        
    except Exception as e:
        logger.error(f"Failed to configure adapter: {e}")
        raise

    # Create recorder instance
    recorder = AudioRecorder()

    # Register the application
    app = GATTApplication()
    bus.export('/org/bluez/example/application', app)

    # Register the service
    service = GATTService()
    bus.export('/org/bluez/example/service0', service)

    # Register the characteristic
    characteristic = GATTCharacteristic(recorder)
    bus.export('/org/bluez/example/characteristic0', characteristic)

    logger.info("BLE services registered")
    return bus, recorder

async def main():
    logger.info("Starting BLE GATT server...")
    
    # Setup D-Bus and BlueZ
    bus, recorder = await setup_bluez()
    
    logger.info("BLE GATT server running...")
    
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        recorder.stop_recording()
        logger.info("Server stopped")

if __name__ == '__main__':
    asyncio.run(main())
