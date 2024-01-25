# -*- coding: utf-8 -*-
"""
Module implements the following classes

    * **TBluetooth**: singleton to drive the bluetooth interface
    * **TUserAccount**: Reads the Windows user account from registry
    * **TEezzyFreeScan**: Scans the bluetooth port for devices to enter or leave the range
    * **TBluetoothService**: Connects to bluetooth-service EEZZ and manages communication

"""
import select
from   threading       import Thread, Lock, Condition
from   table           import TTable

import bluetooth
from   bluetooth       import BluetoothSocket
import json
import time
from   enum             import Enum
import gettext
from   dataclasses      import dataclass
from   itertools        import filterfalse

_ = gettext.gettext


class ReturnCode(Enum):
    TIMEOUT:            1100
    PW_CHECK:           1101
    PW_EVALUATION:      1102
    DEVICE_SELECTION:   1104
    DEVICE_RANGE:       1105
    ATTRIBUTES:         1106
    BT_SERVICE:         1107


@dataclass()
class TMessage:
    code:       int  = None
    value:      str  = None
    message:    dict = None

    def __post_init__(self):
        if self.message:
            self.code  = self.message['return']['code']
            self.value = self.message['return']['value']
        else:
            self.message = {'return': {'code': self.code, 'value': self.value}}

    def __str__(self):
        json.dumps(self.message)


@dataclass(kw_only=True)
class TBluetoothService:
    """ The TBluetoothService handles a connection for a specific mobile given by bt_address """
    eezz_service:   str     = "07e30214-406b-11e3-8770-84a6c8168ea0"
    m_lock:         Lock    = Lock()
    bt_socket               = None
    bt_service:     list    = None
    address:        str     = None

    def __init__(self, address):
        self.connected  = False
        self.address = address
        self.open_connection()

    def open_connection(self):
        if self.connected:
            return
        self.bt_service = bluetooth.find_service(uuid=self.eezz_service, address=self.address)
        if self.bt_service:
            self.bt_socket  = BluetoothSocket(bluetooth.RFCOMM)
            self.bt_socket.connect((self.bt_service[0]['host'], self.bt_service[0]['port']))
            self.connected = True

    def shutdown(self):
        if self.connected:
            self.bt_socket.close()
            self.connected = False

    def send_request(self, message: dict) -> dict:
        if not self.open_connection():
            return {"return": {"code": 500, "value": f'Could not connect to EEZZ service on {self.address}'}}

        try:
            with self.m_lock:
                x_timeout: float = 1.0
                # Send request: Wait for writer and send message
                x_rd, x_wr, x_err = select.select([], [self.bt_socket], [], x_timeout)
                if not x_wr:
                    return {"return": {"code": ReturnCode.TIMEOUT, "value": f'EEZZ service timeout on {self.address}'}}

                for x_writer in x_wr:
                    x_writer.send(json.dumps(message).encode('utf8'))
                    break

                # receive an answer
                x_rd, x_wr, x_err = select.select([self.bt_socket], [], [self.bt_socket], x_timeout)
                if x_err:
                    return {"return": {"code": 514, "value": ''}}

                for x_reader in x_rd:
                    x_result   = x_reader.recv(1024)
                    x_result   = x_result.decode('utf8').split('\n')[-2]
                    return json.loads(x_result)

                return {"return": {"code": ReturnCode.TIMEOUT, "value": f'EEZZ service timeout on {self.address}'}}
        except OSError as xEx:
            self.connected = False
            self.bt_socket.close()
            return {"return": {"code": 514, "value": xEx}}


@dataclass(kw_only=True)
class TBluetooth(TTable):
    """ The bluetooth class manages bluetooth devices in range
    A scan_thread is started to keep looking for new devices.
    TBluetooth service is a singleton to manage this consistently """
    bt_lock:            Lock      = None
    bt_table_changed:   Condition = None

    def __post_init__(self):
        self.column_names = ['Address', 'Name']
        self.title        = 'bluetooth devices'
        super().__post_init__()

        self.bt_lock          = Lock()
        self.bt_table_changed = Condition()
        self.scan_bluetooth   = Thread(target=self.find_devices(), daemon=True, name='find devices').start()

    def find_devices(self) -> None:
        """ Is called frequently by scan_thread to show new devices in range. All access to the list
        has to be thread save in this case
        """
        while True:
            x_result = bluetooth.discover_devices(flush_cache=True, lookup_names=True)
            with self.bt_lock:
                for x in filterfalse(lambda x_data: x_data['Address'] in x_result, self.data):
                    self.data.remove(x)

                for x_item in x_result:
                    x_address, x_name = x_item
                    self.append([x_address, x_name], row_id=x_address, exists_ok=True)

                with self.bt_table_changed:
                    self.bt_table_changed.notify_all()
                time.sleep(2)


# --- Section for module test
def test_bluetooth_table():
    """:meta private:"""
    """ Test the access to the bluetooth environment """
    pass


if __name__ == '__main__':
    """:meta private:"""
    """ Main entry point for module tests """
    test_bluetooth_table()
