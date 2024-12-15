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
                    'Flags': Variant('as', ['read', 'notify', 'encrypt-read', 'encrypt-write'])
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

class Agent(ServiceInterface):
    def __init__(self):
        super().__init__('org.bluez.Agent1')
        
    @method()
    def Release(self):
        logger.info("Agent released")
        
    @method()
    def RequestPinCode(self, device: 'o') -> 's':
        logger.info(f"RequestPinCode for device: {device}")
        return "000000"
        
    @method()
    def DisplayPinCode(self, device: 'o', pincode: 's'):
        logger.info(f"DisplayPinCode {pincode} for device: {device}")
        
    @method()
    def RequestPasskey(self, device: 'o') -> 'u':
        logger.info(f"RequestPasskey for device: {device}")
        return 0
        
    @method()
    def DisplayPasskey(self, device: 'o', passkey: 'u', entered: 'q'):
        logger.info(f"DisplayPasskey {passkey} for device: {device}")
        
    @method()
    def RequestConfirmation(self, device: 'o', passkey: 'u'):
        logger.info(f"RequestConfirmation {passkey} for device: {device}")
        return
        
    @method()
    def RequestAuthorization(self, device: 'o'):
        logger.info(f"RequestAuthorization for device: {device}")
        return
        
    @method()
    def AuthorizeService(self, device: 'o', uuid: 's'):
        logger.info(f"AuthorizeService {uuid} for device: {device}")
        return
        
    @method()
    def Cancel(self):
        logger.info("Cancel")

async def setup_bluez():
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    
    # Configure adapter for advertising
    adapter_path = '/org/bluez/hci0'
    
    # Define Advertisement class
    class Advertisement(ServiceInterface):
        def __init__(self):
            super().__init__('org.bluez.LEAdvertisement1')
            self._type = 'peripheral'
            self._service_uuids = ["12345678-1234-5678-1234-56789abcdef0"]
            self._local_name = 'RaspberryPiAudio'
            
        @dbus_property(access=PropertyAccess.READ)
        def Type(self) -> 's':
            return self._type
            
        @dbus_property(access=PropertyAccess.READ)
        def ServiceUUIDs(self) -> 'as':
            return self._service_uuids
            
        @dbus_property(access=PropertyAccess.READ)
        def LocalName(self) -> 's':
            return self._local_name
    
    # Update introspection data to include pairing properties
    adapter_introspection = '''
        <node>
            <interface name="org.bluez.Adapter1">
                <property name="Powered" type="b" access="readwrite"/>
                <property name="Discoverable" type="b" access="readwrite"/>
                <property name="DiscoverableTimeout" type="u" access="readwrite"/>
                <property name="Pairable" type="b" access="readwrite"/>
                <property name="PairableTimeout" type="u" access="readwrite"/>
                <property name="Alias" type="s" access="readwrite"/>
            </interface>
            <interface name="org.bluez.LEAdvertisingManager1">
                <method name="RegisterAdvertisement">
                    <arg name="advertisement" type="o" direction="in"/>
                    <arg name="options" type="a{sv}" direction="in"/>
                </method>
            </interface>
            <interface name="org.freedesktop.DBus.Properties">
                <method name="Get">
                    <arg name="interface" type="s" direction="in"/>
                    <arg name="property" type="s" direction="in"/>
                    <arg name="value" type="v" direction="out"/>
                </method>
                <method name="Set">
                    <arg name="interface" type="s" direction="in"/>
                    <arg name="property" type="s" direction="in"/>
                    <arg name="value" type="v" direction="in"/>
                </method>
            </interface>
            <interface name="org.bluez.AgentManager1">
                <method name="RegisterAgent">
                    <arg name="agent" type="o" direction="in"/>
                    <arg name="capability" type="s" direction="in"/>
                </method>
                <method name="RequestDefaultAgent">
                    <arg name="agent" type="o" direction="in"/>
                </method>
            </interface>
        </node>
    '''
    
    # Get proxy object with introspection data
    proxy_obj = bus.get_proxy_object('org.bluez', adapter_path, adapter_introspection)
    properties = proxy_obj.get_interface('org.freedesktop.DBus.Properties')
    le_advertising = proxy_obj.get_interface('org.bluez.LEAdvertisingManager1')
    agent_manager = proxy_obj.get_interface('org.bluez.AgentManager1')
    
    # Enable adapter, pairing, and advertising
    try:
        # Power on and configure adapter
        await properties.call_set('org.bluez.Adapter1', 'Powered', Variant('b', True))
        await properties.call_set('org.bluez.Adapter1', 'Discoverable', Variant('b', True))
        await properties.call_set('org.bluez.Adapter1', 'DiscoverableTimeout', Variant('u', 0))
        await properties.call_set('org.bluez.Adapter1', 'Pairable', Variant('b', True))
        await properties.call_set('org.bluez.Adapter1', 'PairableTimeout', Variant('u', 0))
        
        logger.info("Bluetooth adapter configured with pairing enabled")
        
        # Create and register advertisement
        advertisement = Advertisement()
        bus.export('/org/bluez/example/advertisement0', advertisement)
        await le_advertising.call_register_advertisement('/org/bluez/example/advertisement0', {})
        
        logger.info("Bluetooth LE advertising enabled with custom service UUID")
        
        # Register agent for handling pairing
        agent = Agent()
        bus.export('/org/bluez/example/agent', agent)
        await agent_manager.call_register_agent('/org/bluez/example/agent', 'NoInputNoOutput')
        await agent_manager.call_request_default_agent('/org/bluez/example/agent')
        logger.info("Bluetooth agent registered for 'Just Works' pairing")
        
    except Exception as e:
        logger.error(f"Failed to configure advertising: {e}")
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

    logger.info("BLE services registered and advertising")
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
