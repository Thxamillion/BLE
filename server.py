import asyncio
import os
import logging
from dbus_next.aio import MessageBus
from dbus_next.service import ServiceInterface, method, dbus_property, signal, Variant
from dbus_next.constants import BusType, PropertyAccess
from record import AudioRecorder, logger

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
                    'Flags': Variant('as', ['read', 'notify']),
                    'Value': Variant('ay', b'')
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
        self._value = b''
        self.recorder = recorder
        self._clients = set()
        logger.info("GATTCharacteristic initialized")

    @dbus_property(access=PropertyAccess.READ)
    def UUID(self) -> 's':
        return self._uuid

    @dbus_property(access=PropertyAccess.READ)
    def Service(self) -> 'o':
        return self._service

    @dbus_property(access=PropertyAccess.READ)
    def Flags(self) -> 'as':
        return self._flags

    @dbus_property(access=PropertyAccess.READ)
    def Value(self) -> 'ay':
        return self._value

    @method()
    def ReadValue(self, options: 'a{sv}') -> 'ay':
        next_file = self.recorder.get_next_file()
        if next_file:
            try:
                with open(next_file, 'rb') as f:
                    chunk = f.read(512)
                    if chunk:
                        logger.info(f"Sending chunk of size {len(chunk)} bytes")
                        return list(chunk)
                    os.remove(next_file)
                    logger.info(f"File transfer complete, deleted: {next_file}")
            except Exception as e:
                logger.error(f"Error sending file {next_file}: {e}")
        return []

    @method()
    def StartNotify(self):
        sender = self.get_sender()
        logger.info("=" * 50)
        logger.info(f"StartNotify called!")
        logger.info(f"New client connected!")
        logger.info(f"Client ID: {sender}")
        logger.info("Starting audio recording...")
        logger.info("=" * 50)
        self._clients.add(sender)
        if len(self._clients) == 1:  # First client connected
            logger.info("First client connected, starting recorder")
            self.recorder.start_recording()
        else:
            logger.info(f"Additional client connected. Total clients: {len(self._clients)}")

    @method()
    def StopNotify(self):
        sender = self.get_sender()
        logger.info("=" * 50)
        logger.info(f"Client disconnected!")
        logger.info(f"Client ID: {sender}")
        logger.info("=" * 50)
        self._clients.discard(sender)
        if not self._clients:  # No more clients connected
            self.recorder.stop_recording()

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
        </node>
    '''
    
    # Get proxy object with introspection data
    proxy_obj = bus.get_proxy_object('org.bluez', adapter_path, adapter_introspection)
    properties = proxy_obj.get_interface('org.freedesktop.DBus.Properties')
    le_advertising = proxy_obj.get_interface('org.bluez.LEAdvertisingManager1')
    
    try:
        # Power on and configure adapter
        await properties.call_set('org.bluez.Adapter1', 'Powered', Variant('b', True))
        await properties.call_set('org.bluez.Adapter1', 'Discoverable', Variant('b', True))
        await properties.call_set('org.bluez.Adapter1', 'DiscoverableTimeout', Variant('u', 0))
        
        logger.info("Bluetooth adapter configured")
        
        # Create and register advertisement
        advertisement = Advertisement()
        bus.export('/org/bluez/example/advertisement0', advertisement)
        await le_advertising.call_register_advertisement('/org/bluez/example/advertisement0', {})
        
        logger.info("Bluetooth LE advertising enabled with custom service UUID")
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
    
    logger.info("=" * 50)
    logger.info("BLE GATT server running...")
    logger.info("Waiting for connections...")
    logger.info("Service UUID: 12345678-1234-5678-1234-56789abcdef0")
    logger.info("Characteristic UUID: abcdef01-1234-5678-1234-56789abcdef0")
    logger.info("=" * 50)
    
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        recorder.stop_recording()
        logger.info("Server stopped")

if __name__ == '__main__':
    asyncio.run(main()) 