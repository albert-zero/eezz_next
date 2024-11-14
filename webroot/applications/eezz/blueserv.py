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
import asyncio

_ = gettext.gettext


@dataclass(kw_only=True)
class TBluetoothService:
    """
    This class is responsible for managing Bluetooth connections and communication with the EEZZ service.

    The TBluetoothService class encapsulates the functionality for establishing, maintaining, and terminating
    Bluetooth connections. It uses the EEZZ service for communication, indicated by a specific GUID. Apart from
    managing connections, it provides mechanisms for sending requests and handling responses from the EEZZ service.
    It also includes error handling for various connection states and exceptions.

    :ivar address:      Address of the Bluetooth device to connect to.
    :type address:      str
    :ivar eezz_service: Service GUID of the EEZZ app.
    :type eezz_service: str
    :ivar m_lock:       A lock to synchronize communication with the Bluetooth service.
    :type m_lock:       Lock
    :ivar bt_socket:    The communication socket once established.
    :type bt_socket:    BluetoothSocket
    :ivar bt_service:   List of EEZZ services for establishing a connection.
    :type bt_service:   list
    :ivar connected:    Indicates whether the connection to the EEZZ service is established.
    :type connected:    bool
    """
    address:        str                  #: :meta private:
    eezz_service:   str        = "07e30214-406b-11e3-8770-84a6c8168ea0"  # :meta private: service GUID of the eezz App
    m_lock:         Lock       = Lock()  #: :meta private: Sync communication with bluetooth service
    bt_service:     list       = None    #: :meta private: List of eezz service App for establishing a connection
    connected:      bool       = False   #: :meta private: Indicates whether connection to eezz service is established
    bt_socket:      BluetoothSocket  = None    #: :meta private: The communication socket once established

    def __post_init__(self):
        """ :meta private: """
        self.error_codes = {'open_connection':   (700, 'Could not connect to EEZZ service on address={address}'),
                            'timeout':           (701, 'EEZZ service timeout on address={address}'),
                            'connection_closed': (702, 'Connection closed by peer'),
                            'communication':     (703, 'Connection closed during communication with exception {exception}')}

    def open_connection(self):
        """ :meta private:
        Open a bluetooth connection """
        if self.connected:
            return
        self.bt_service = bluetooth.find_service(uuid=self.eezz_service, address=self.address)
        if self.bt_service:
            self.bt_socket  = BluetoothSocket(bluetooth.RFCOMM)
            self.bt_socket.connect((self.bt_service[0]['host'], self.bt_service[0]['port']))
            self.connected = True

    def shutdown(self):
        """ :meta private:
        Shutdown interrupts open connections, stops the port-select and closes all open sockets. """
        if self.connected:
            self.bt_socket.close()
            self.connected = False

    def send_request(self, command: str, args: list) -> dict:
        """
        Sends a request to a remote server via a Bluetooth socket.

        This function attempts to open a connection, format the request message,
        send the message through a Bluetooth socket, and wait for a response. If
        any issue occurs during these steps, it handles the errors appropriately
        and returns a corresponding error message.

        :param command: The command to be sent to the remote server
        :type command:  str
        :param args:    List of arguments associated with the command
        :type args:     list
        :return:        A dictionary with the result of the operation, containing either
                        the server's response or an error code and description
        :rtype:         dict
        """
        if not self.open_connection():
            x_code, x_text = self.error_codes['open_connection']
            return {"return": {"code": x_code, "value": x_text.format(address=self.address)}}

        message = {'command': command, 'args': args}
        try:
            with self.m_lock:
                x_timeout: float = 1.0
                # Send request: Wait for writer and send message
                x_rd, x_wr, x_err = select.select([], [self.bt_socket], [], x_timeout)
                if not x_wr:
                    x_code, x_text = self.error_codes['timeout']
                    return {"return": {"code": x_code, "value": x_text.format(address=self.address)}}

                for x_writer in x_wr:
                    x_writer.send(json.dumps(message).encode('utf8'))
                    break

                # receive an answer
                x_rd, x_wr, x_err = select.select([self.bt_socket], [], [self.bt_socket], x_timeout)
                if x_err:
                    x_code, x_text = self.error_codes['connection_closed']
                    return {"return": {"code": x_code, "value": x_text}}

                for x_reader in x_rd:
                    x_result   = x_reader.recv(1024)
                    x_result   = x_result.decode('utf8').split('\n')[-2]
                    return json.loads(x_result)

                x_code, x_text = self.error_codes['timeout']
                return {"return": {"code": x_code, "value": x_text.format(address=self.address)}}
        except OSError as xEx:
            self.connected = False
            self.bt_socket.close()
            x_code, x_text = self.error_codes['communication']
            return {"return": {"code": x_code, "value": x_text.format(exception=repr(xEx))}}


@dataclass(kw_only=True)
class TBluetooth(TTable):
    """ The bluetooth class manages bluetooth devices in range
    A scan_thread is started to keep looking for new devices.
    If there are any changes, self.async_condition.notif_all is triggered.
    The inherited attributes for column_names and title are fixed to constant values
    """
    column_names:       List[str] = None    #: :meta private: Constant list ['Address', 'Name']
    title:              str       = None    #: :meta private: Constant title 'bluetooth devices'

    def __post_init__(self):
        self.column_names = ['Address', 'Name']
        self.title        = 'bluetooth devices'
        super().__post_init__()
        self.scan_bluetooth = Thread(target=self.find_devices, daemon=True, name='find devices')
        self.scan_bluetooth.start()

    def find_devices(self) -> None:
        """
        Manages discovery of nearby Bluetooth devices and maintains an internal list
        of these devices, notifying when there are changes.

        This is a time-consuming function and should run in a separate thread.
        The caller may wait for changes on TTable.async_condition.
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
        """
        Retrieves the visible rows from the table. If `get_all` is set to True,
        it retrieves all the rows regardless of their visibility.

        :param get_all: Boolean flag to indicate whether to retrieve all rows
                        or only visible rows, defaults to False
        :type get_all:  bool
        :return:        List of visible rows or all rows depending on the `get_all` flag
        :rtype:         List[TTableRow]
        """
        with self.async_lock:
            return super().get_visible_rows(get_all=get_all)


# --- Section for module test
def test_bluetooth_table():
    """:meta private:"""
    """ Test the access to the bluetooth environment """
    bt = TBluetooth()

    # Wait for the table to change
    with bt.async_condition:
        bt.async_condition.wait()
    bt.print()


if __name__ == '__main__':
    """:meta private:"""
    """ Main entry point for module tests """
    test_bluetooth_table()
