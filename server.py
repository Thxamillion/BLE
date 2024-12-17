import asyncio
import os
import logging
from dbus_next.aio import MessageBus
from dbus_next.service import ServiceInterface, method, dbus_property, signal, Variant
from dbus_next.constants import BusType, PropertyAccess
from record import AudioRecorder, logger
from dbus_next import DBusError

class InvalidArgsException(DBusError):
    def __init__(self):
        super().__init__('org.freedesktop.DBus.Error.InvalidArgs', 'Invalid arguments')

class NotSupportedException(DBusError):
    def __init__(self):
        super().__init__('org.bluez.Error.NotSupported', 'Operation not supported')

class NotPermittedException(DBusError):
    def __init__(self):
        super().__init__('org.bluez.Error.NotPermitted', 'Operation not permitted')

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
                    'Flags': Variant('as', ['read', 'notify', 'encrypt-read', 'encrypt-write']),
                    'Value': Variant('ay', b'')
                }
            }
        }

class GATTService(ServiceInterface):
    PATH_BASE = '/org/bluez/example/service'

    def __init__(self, bus, index):
        self.path = self.PATH_BASE + str(index)
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
        self._flags = ['read', 'notify', 'encrypt-read', 'encrypt-write']
        self._service = '/org/bluez/example/service0'
        self._value = b''
        self.recorder = recorder
        self._clients = set()
        self.notifying = False

    @signal()
    def PropertiesChanged(self, interface: 's', changed: 'a{sv}', invalidated: 'as'):
        pass

    def notify_value(self, value):
        if not self.notifying:
            return
        self.PropertiesChanged(
            'org.bluez.GattCharacteristic1',
            {'Value': Variant('ay', bytes(value))},
            []
        )

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

    @method(name='ReadValue')
    def read_value(self, options: 'a{sv}') -> 'ay':
        try:
            next_file = self.recorder.get_next_file()
            if next_file:
                with open(next_file, 'rb') as f:
                    chunk = f.read(512)
                    if chunk:
                        logger.info(f"Sending chunk of size {len(chunk)} bytes")
                        return bytes(chunk)
                    os.remove(next_file)
                    logger.info(f"File transfer complete, deleted: {next_file}")
            return b''
        except Exception as e:
            logger.error(f"Error in ReadValue: {e}")
            raise NotSupportedException()

    @method(name='StartNotify')
    def start_notify(self) -> None:
        sender = self.get_sender()
        logger.info("=" * 50)
        logger.info(f"StartNotify called!")
        logger.info(f"New client connected!")
        logger.info(f"Client ID: {sender}")
        logger.info(f"Current number of clients: {len(self._clients)}")
        logger.info(f"Is recording already?: {self.recorder.is_recording}")
        self._clients.add(sender)
        if len(self._clients) == 1:  # First client connected
            logger.info("First client connected, starting recorder")
            try:
                self.recorder.start_recording()
                logger.info("Recording started successfully")
            except Exception as e:
                logger.error(f"Failed to start recording: {e}")
        else:
            logger.info(f"Additional client connected. Total clients: {len(self._clients)}")
        logger.info("=" * 50)

    @method(name='StopNotify')
    def stop_notify(self) -> None:
        sender = self.get_sender()
        logger.info("=" * 50)
        logger.info(f"Client disconnected!")
        logger.info(f"Client ID: {sender}")
        logger.info("=" * 50)
        self._clients.discard(sender)
        if not self._clients:  # No more clients connected
            self.recorder.stop_recording()

    @method(name='WriteValue')
    def write_value(self, value: 'ay', options: 'a{sv}') -> None:
        logger.info(f"WriteValue called with: {bytes(value).decode()}")

    @method(name='GetAll')
    def get_all(self, interface: 's') -> 'a{sv}':
        if interface != 'org.bluez.GattCharacteristic1':
            raise InvalidArgsException()

        return {
            'UUID': self._uuid,
            'Service': self._service,
            'Flags': self._flags,
            'Value': self._value
        }

async def setup_bluez():
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    
    # Configure adapter for advertising
    adapter_path = '/org/bluez/hci0'
    
    # Update introspection data with available properties
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
        # Configure adapter for secure connections
        await properties.call_set('org.bluez.Adapter1', 'Powered', Variant('b', True))
        await properties.call_set('org.bluez.Adapter1', 'Discoverable', Variant('b', True))
        await properties.call_set('org.bluez.Adapter1', 'DiscoverableTimeout', Variant('u', 0))
        await properties.call_set('org.bluez.Adapter1', 'Pairable', Variant('b', True))
        await properties.call_set('org.bluez.Adapter1', 'PairableTimeout', Variant('u', 0))
        await properties.call_set('org.bluez.Adapter1', 'Alias', Variant('s', 'RaspberryPiAudio'))
        
        logger.info("Bluetooth adapter configured for secure connections")
        
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

    # Register the service with bus and index
    service = GATTService(bus, 0)  # Pass bus and index 0
    bus.export('/org/bluez/example/service0', service)

    # Register the characteristic
    characteristic = GATTCharacteristic(recorder)
    bus.export('/org/bluez/example/characteristic0', characteristic)
    logger.info(f"Registered characteristic with UUID: {characteristic._uuid}")
    logger.info(f"Characteristic flags: {characteristic._flags}")
    logger.info(f"Characteristic service: {characteristic._service}")

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
            logger.debug("Server heartbeat")  # To verify the server is still running
            await asyncio.sleep(10)  # Heartbeat every 10 seconds
    except KeyboardInterrupt:
        recorder.stop_recording()
        logger.info("Server stopped")

if __name__ == '__main__':
    asyncio.run(main())
