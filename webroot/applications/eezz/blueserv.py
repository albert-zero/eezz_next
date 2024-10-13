# -*- coding: utf-8 -*-
"""
The module bueserv handles the bluetooth features of EEZZ.
and implements the following classes

    * :py:class:`eezz.table.TBluetooth`:        TTable for listing bluetooth devices in range
    * :py:class:`eezz.table.TBluetoothService`: Communicates with bluetooth-service EEZZ on mobile device

"""
import select
from   threading       import Thread, Lock, Condition
from   table           import TTable, TTableRow

import bluetooth
from   bluetooth       import BluetoothSocket

import json
import time
import gettext
from   dataclasses      import dataclass
from   itertools        import filterfalse
from   typing           import List

_ = gettext.gettext


@dataclass(kw_only=True)
class TBluetoothService:
    """ The TBluetoothService handles a connection for a specific mobile given by address.
    The class is defined as dataclass, so that the call parameters become properties.

    :ivar eezz_service:    The service GUID of the eezz App
    :ivar m_lock:          Lock communication for a single request/response cycle
    :ivar bt_socket:       The communication socket
    :ivar bt_service:      The services associated with the eezz App
    :ivar connected:       Indicates that the service is active
    """
    address:       str
    """ Property - The address of the bluetooth device """
    eezz_service:  str        = "07e30214-406b-11e3-8770-84a6c8168ea0"
    """ :meta private: """
    m_lock:        Lock       = Lock()
    """ :meta private: """
    bt_socket                 = None
    """ :meta private: """
    bt_service:    list       = None
    """ :meta private: """
    connected:     bool       = False
    """ :meta private: """

    def open_connection(self):
        """ Open a bluetooth connection
        """
        if self.connected:
            return
        self.bt_service = bluetooth.find_service(uuid=self.eezz_service, address=self.address)
        if self.bt_service:
            self.bt_socket  = BluetoothSocket(bluetooth.RFCOMM)
            self.bt_socket.connect((self.bt_service[0]['host'], self.bt_service[0]['port']))
            self.connected = True

    def shutdown(self):
        """ Shutdown interrupts open connections, stops the port-select and closes all open sockets.
        """
        if self.connected:
            self.bt_socket.close()
            self.connected = False

    def send_request(self, command: str, args: list) -> dict:
        """ A request is sent to the device, waiting for a response.
        The protocol use EEZZ-JSON structure:
        send ->    {message: str, args: list}
        receive -> {return: dict { code: int, value: str}, ...}

        :param command: The command to execute
        :param args: The arguments for the given command
        :return: JSON structure send by device
        """
        if not self.open_connection():
            return {"return": {"code": 500, "value": f'Could not connect to EEZZ service on {self.address}'}}

        message = {'command': command, 'args': args}
        try:
            with self.m_lock:
                x_timeout: float = 1.0
                # Send request: Wait for writer and send message
                x_rd, x_wr, x_err = select.select([], [self.bt_socket], [], x_timeout)
                if not x_wr:
                    return {"return": {"code": 500, "value": f'EEZZ service timeout on {self.address}'}}

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

                return {"return": {"code": 500, "value": f'EEZZ service timeout on {self.address}'}}
        except OSError as xEx:
            self.connected = False
            self.bt_socket.close()
            return {"return": {"code": 514, "value": xEx}}


@dataclass(kw_only=True)
class TBluetooth(TTable):
    """ The bluetooth class manages bluetooth devices in range
    A scan_thread is started to keep looking for new devices.
    TBluetooth service is a singleton to manage this consistently

    :ivar column_names:     Constant list ['Address', 'Name']
    :ivar title:            Constant title = bluetooth devices
    :ivar bt_table_changed: Condition: Signals table change events
    """
    column_names:      List[str] = None
    """ :meta private: """
    bt_table_changed = Condition()
    """ :meta private: """

    def __post_init__(self):
        self.column_names = ['Address', 'Name']
        self.title        = 'bluetooth devices'
        super().__post_init__()
        self.scan_bluetooth   = Thread(target=self.find_devices(), daemon=True, name='find devices').start()

    def find_devices(self) -> None:
        """ This method is called frequently by thread `self.scan:bluetooth` to keep track of devices in range.
        The table is checked for new devices or devices which went out of range. Only if the list changes the
        condition TTable.async_condition is triggered with notify_all.
        """
        while True:
            x_result = bluetooth.discover_devices(flush_cache=True, lookup_names=True)

            with self.async_lock:
                table_changed = False

                # Step 1: Reduce the internal list to devices in range
                for x in filterfalse(lambda x_data: (x_data[0], x_data[1]) in x_result, self.data):
                    self.data.remove(x)
                    table_changed = True

                # Step 2: Check for new entries
                x_stored_devices = [(x[0], x[1]) for x in self.data]
                for x in filterfalse(lambda x_data: x_data in x_stored_devices, x_result):
                    self.append([x[0], x[1]], row_id=x[0], exists_ok=True)
                    table_changed = True

                if table_changed:
                    with self.async_condition:
                        self.async_condition.notify_all()
            # wait a bit for next scan
            time.sleep(2)

    def get_visible_rows(self, get_all: bool = False) -> List[TTableRow]:
        with self.async_lock:
            return super().get_visible_rows(get_all=get_all)


# --- Section for module test
def test_bluetooth_table():
    """:meta private:"""
    """ Test the access to the bluetooth environment """
    pass


if __name__ == '__main__':
    """:meta private:"""
    """ Main entry point for module tests """
    test_bluetooth_table()
