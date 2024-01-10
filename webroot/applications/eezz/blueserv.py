# -*- coding: utf-8 -*-
"""
    Copyright (C) 2015 www.EEZZ.biz (haftungsbeschränkt)

    TBluetooth:
    singleton to drive the bluetooth interface
    

"""
import inspect
import queue
from   pathlib import Path
import logging
import linecache
import os
import ctypes
import select
from   queue           import Queue
from   threading       import Thread, Lock, Condition
from   table           import TTable, TTableRow
from   service         import singleton

import bluetooth
from   bluetooth       import BluetoothSocket
import base64

from   Crypto          import Random
from   Crypto.Cipher    import AES

from   itertools       import zip_longest
import json
import winreg
import struct
import time
from   Crypto.PublicKey import RSA
from   Crypto.Cipher    import PKCS1_v1_5
from   Crypto.Hash      import MD5
from   enum             import Enum
import gettext
from   document         import TMobileDevices
from   dataclasses      import dataclass
from   typing           import Tuple, Any, Callable
from   seccom           import TSecureSocket

_ = gettext.gettext


class ReturnCode(Enum):
    TIMEOUT:            1100
    PW_CHECK:           1101
    PW_EVALUATION:      1102
    DEVICE_SELECTION:   1104
    DEVICE_RANGE:       1105
    ATTRIBUTES:         1106
    BT_SERVICE:         1107


@singleton
@dataclass(kw_only=True)
class TMessage:
    message: dict = None

    def __post_init__(self):
        self.message = {
            1000: 'Device not in range'
        }


@singleton
class TBluetooth(TTable):
    """ The bluetooth class manages bluetooth devices in range
    A scan_thread is started to keep looking for new devices.
    TBluetooth service is a singleton to manage this consistently """
    def __init__(self):
        # noinspection PyArgumentList
        super().__init__(column_names=['Address', 'Name'], title='bluetooth devices')
        self.m_lock        = Lock()
        self.easy_free_lock_thread: Thread | None = None
        self.easy_free_scan_thread: Thread | None = None

        self.easy_free_scan_thread = TEezzyFreeScan()
        self.easy_free_scan_thread.start()
        self.condition = Condition()
        self.bt_service_threads = dict()
        self.bt_credentials = None

    def connect(self, credentials: dict) -> None:
        """ Connect to existing setup. If the credentials are not sufficient, we just ignore the call
        :param credentials: Dictionary with entry 'sid' and 'name' as list of just one entry
        """
        try:
            self.bt_credentials = credentials
            x_sid  = credentials['sid']
            x_rows = TMobileDevices().get_visible_rows(filter_row=lambda x: x['CSid'] == x_sid[0])
            if x_rows:
                self.bt_credentials['address'] = x_rows[0]['CAddr']
        except (KeyError, TypeError):
            self.bt_credentials = None

    def find_devices(self) -> None:
        """ Is called frequently by scan_thread to show new devices in range. All access to the list
         has to be thread save in this case
         If a coupled devices is detected, the eezzy free process is started. The coupled address is used
         to retrieve the data for workstation unlock """
        x_result = bluetooth.discover_devices(flush_cache=True, lookup_names=True)
        with self.m_lock:
            self.data.clear()
            for x_item in x_result:
                x_address, x_name = x_item
                self.append([x_address, x_name])
                self.start_bluetooth(x_address)
            self.do_sort(0)
            with self.condition:
                self.condition.notify_all()

    def start_bluetooth(self, address: str) -> bool:
        """  Check if the connection to device already exists, which is ensured by a running eezzy-free thread, which
        sends frequently a PING request.
        :param address: The address to connect to if not already done
        """
        if self.bt_credentials is None or self.bt_credentials['address'] != address:
            return False

        x_thread = self.bt_service_threads.get(address)
        if not (x_thread and x_thread.is_alive()):
            x_thread = TQueueRequest(address)
            x_thread.start()
            self.bt_service_threads[address] = x_thread
        return True

    def get_coupled_user(self, credentials: dict) -> dict | None:
        """ Called by external process to unlock workstation
        :param credentials: The SID to crosscheck the current connection
        :return: The password to unlock the workstation
        """
        if not self.bt_credentials or \
                not self.bt_credentials.get('address') or \
                not self.bt_credentials['sid'] == credentials['sid']:
            return {'result': {'code': 500}}

        x_address = self.bt_credentials['address']
        x_row     = TMobileDevices().get_couple(x_address)
        # ['CAddr', 'CDevice', 'CSid', 'CUser', 'CAddr', 'CVector', 'CKey']
        x_addr, x_device, x_sid, x_user, x_sid, x_vector64, x_rsa_key = x_row.get_values_list()
        # todo check x_sid == sid
        # - if the lock daemon is hanging on the wrong mobile, this is the time to correct the setup
        #

        x_rsa_key = RSA.importKey(x_rsa_key)
        x_vector  = base64.b64decode(x_vector64)

        # Send password request to the device:
        x_response = self.bt_service.send_request(x_addr, {"command": "GETUSR", "args": [x_sid]})
        if x_response['return']['code'] != 200:
            return {'result': {'code': 500}}

        try:
            x_jsn_pwd_enc64 = x_response['return']['encrypted']
            x_jsn_pwd_encr  = base64.b64decode(x_jsn_pwd_enc64)
            x_encryptor     = PKCS1_v1_5.new(x_rsa_key)
            x_jsn_pwd_str   = x_encryptor.decrypt(x_jsn_pwd_encr, x_vector).decode('utf8')
            # xJsnPwdStr    = xRsaKey.decrypt(xJsnPwdEncr).decode('utf-8')

            x_jsn_pwd       = json.loads(x_jsn_pwd_str)
            x_pwd_encr64    = x_jsn_pwd['password'].encode('utf-8')
            x_pwd_encr      = base64.b64decode(x_pwd_encr64)
            x_timestamp     = x_jsn_pwd['time']

            # Define a timeout for secure communication
            if abs(time.time() - x_timestamp / 1000) > 100:
                # x_result =  {"return": {"code": ErrorCodes.TIMEOUT.value, "value": "timeout"}}
                return {}

            x_pwd_clear = b''.join([bytes([(x ^ y) & 0xff]) for x, y in zip(x_pwd_encr, x_vector)])
            x_pwd_clear = x_pwd_clear[:].decode('utf-8')
            x_response = {"address": x_addr,"return": {"code": 200, "value": "GETUSR", "args": [x_sid, x_pwd_clear]}}
            return x_response
        except Exception as x_ex:
            return {"return": {"code": 1120, "value": x_ex}}

    def set_coupled_user(self, device_address: str, user: str, password: str) -> dict:
        """ Stores the user password on mobile device. The password is encrypted and the key is stored in the
        windows registry.
        :param device_address: Address of the mobile device as returned by find_devices
        :param device_name: Name of the mobile as returned by find_address
        :param user: Name of the user in the windows registry
        :param sid: SID is the user-id stored with the user in the registry
        :param password: Password will be encrypted and stored on device for unlock workstation
        :return: EEZZ Confirmation message as dict
        """
        x_device_address = self.bt_credentials.get('address')
        x_device_name    = self.bt_credentials.get('device_name')
        x_device_sid     = self.bt_credentials.get('device_sid')
        x_vector      = Random.new().read(16)
        x_vector64    = base64.b64encode(x_vector)
        x_pwd_encr    = b''.join([bytes([(int(x) ^ y) & 0xff]) for x, y in zip_longest(password.encode('utf8'), x_vector, fillvalue=0)])
        x_pwd_encr64  = base64.b64encode(x_pwd_encr).decode('utf8')
        try:
            # Store the base vector in registry as alternative retrieval of password
            x_device_hdl = winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, 'Software\\eezz\\assoc', 0, winreg.KEY_SET_VALUE)
            winreg.SetValueEx(x_device_hdl, sid, None, winreg.REG_BINARY, int.from_bytes(x_vector))
        except OSError as xEx:
            pass

        # Generate key for this couple
        # Public key is send to device, private key is stored on PC
        x_rsa_key     = RSA.generate(1024)
        x_priv_key    = x_rsa_key.exportKey()
        x_pub_key     = x_rsa_key.publickey().exportKey().decode('utf8')
        x_pub_key_str = ''.join([xLine for xLine in x_pub_key.split('\n') if not ('----' in xLine)])
        x_response    = self.bluetooth_request({"command": "SETUSR", "args": [x_device_sid, x_pwd_encr64, x_pub_key_str]})

        # insert the public key into local database
        if x_response['return']['code'] == 200:
            # :address, :device, sid, :user, :vector, :key
            TMobileDevices().db_insert(row_data={'address': x_device_address, 'device': x_device_name, 'sid': x_device_sid, 'user': user, 'vector64': x_vector64, 'key': x_priv_key})
            x_response['return']['args'] = [_("Password successfully stored on device")]
        return x_response

    def register_user(self, address: str, alias: str = '', fname: str = '', lname: str = '', email: str = '', iban: str = '', password: str = '') -> dict:
        """ Register user on EEZZ server. The request is send to the mobile device, which enriches the data and then
        forwards it to the eezz server page.
        :param address:  Address of the mobile device
        :param alias:    Display name of the user
        :param fname:    First name
        :param lname:    Last name
        :param email:    E-Mail address
        :param iban:     Payment account
        :param password: Password for the service. Only the hash value is stored, not the password itself
        :return:         Status message
        """
        # get coupled user for a given address:
        if not email or not iban or not password or not alias:
            x_response = {"return": {"code": ReturnCode.ATTRIBUTES.value, "value": "mandatory fields missing"}}
            return x_response

        x_hash_md5 = MD5.new(password.encode('utf8'))
        x_address  = address
        x_request  = {"command": "Register",
                      "args":  [x_hash_md5.hexdigest(), ''],
                      "TUser": {"CEmail": email, "CNameAlias": alias, "CNameFirst": fname, "CNameLast": lname, "CIban": iban}}
        x_response = self.bluetooth_request(address=x_address, command=x_request)
        if x_response['return']['code'] == 200:
            # self.mDatabase.setSim(x_address, x_response['TDevice']['CSim'])
            x_response['return']['args'] = [_('Reply to verification E-Mail to accomplish registration')]
        return x_response

    def bluetooth_request(self, command: dict, address=None) -> dict:
        """ launch request to the mobile device like {"command": "GETKEY", "args": []}
        :param command:
        :param address:
        :return:
        """
        x_address = address
        if not x_address:
            x_address = self.bt_credentials['address']
        if not x_address:
            return {'return': {'code': 500}}

        x_thread = self.bt_service_threads.get(address)
        if not (x_thread and not x_thread.is_alive()):
            return {'return': {'code': 500}}

        x_thread.request_queue.put(command, block=True)
        x_response = x_thread.response_queue.get(block=True)
        return x_response

    def get_visible_rows(self, get_all: bool = False, filter_row: Callable = lambda x: x) -> list:
        """ thread save access to the table entries
        :param filter_row:
        :param filter_column: Filter for a named column
        :param get_all: Get more elements than visible_rows if set to True
        :return: All table rows in the view
        """
        with self.m_lock:
            return super().get_visible_rows(get_all=True, filter_row=filter_row)

    def decrypt_key(self, encrypted_key: bytes) -> bytes:
        # Get the device key for decryption
        x_response          = self.bluetooth_request(command={"command": "GETKEY", "args": []})
        x_dev_keyvector64   = x_response['return']['value']
        x_dev_keyvector     = base64.b64decode(x_dev_keyvector64)

        x_encrypted_key_64  = encrypted_key
        x_encrypted_key     = base64.b64decode(x_encrypted_key_64)

        x_cipher            = AES.new(x_dev_keyvector[16:], AES.MODE_CBC, x_dev_keyvector[:16])
        x_doc_keyvector     = x_cipher.decrypt(x_encrypted_key)
        x_doc_keyvector64   = base64.b64encode(x_doc_keyvector)
        return x_doc_keyvector64

    def encrypt_key(self, key: bytes, vector: bytes) -> bytes:
        # Get the device key for encryption
        x_response          = self.bluetooth_request(command={"command": "GETKEY", "args": []})
        x_dev_keyvector64   = x_response['return']['value']
        x_dev_keyvector     = base64.b64decode(x_dev_keyvector64)

        x_doc_keyvector     = base64.b64decode(key + vector)
        x_cipher            = AES.new(x_dev_keyvector[16:], AES.MODE_CBC, x_dev_keyvector[:16])
        x_encrypted_key     = x_cipher.encrypt(x_doc_keyvector)
        x_encrypted_key64   = base64.b64encode(x_encrypted_key)
        return x_encrypted_key64

    def register_document(self, document_key: bytes, document_header: dict) -> dict:
        # Store and sign the document header using the document-key and the device-sim
        x_eezz_connection   = TSecureSocket()
        x_response          = x_eezz_connection.send_request('reqheader', [self.bt_credentials.get('sim'), document_key], document_header)
        x_json_response     = json.loads(x_response.decode('utf-8'))
        return x_json_response

    def get_document_info(self, document_id: bytes, buy_request=False) -> dict:
        x_device_sim        = self.bt_credentials['sim']
        x_eezz_connection   = TSecureSocket()
        x_response          = x_eezz_connection.send_request('reqkey', [document_id, x_device_sim, int(buy_request)])
        x_json_response     = json.loads(x_response.decode('utf-8'))

        x_crpyt_key         = x_json_response.get('key')
        x_transaction_key   = x_json_response.get('transaction')

        if x_crpyt_key:
            x_document_key = self.decrypt_key(x_crpyt_key)
            x_json_response['return'] = {'code': 200, 'value': x_document_key}
        elif x_transaction_key:
            x_json_response['return'] = {'code': 100, 'value': x_transaction_key}
        return x_json_response

    def buy_document(self, transaction_key: bytes):
        x_eezz_connection   = TSecureSocket()
        x_response          = x_eezz_connection.send_request('reqkeycommit', [transaction_key])
        x_json_response     = json.loads(x_response.decode('utf-8'))
        x_encrypted_key     = x_json_response['key']
        x_document_key      = self.decrypt_key(x_encrypted_key)
        x_json_response['return'] = {'code': 200, 'value': x_document_key}
        return x_json_response

    def add_document(self, document_id: bytes, document_key: bytes) -> dict:
        x_response = self.bluetooth_request({"command": "ADDDOC", "args": [document_id, document_key]})
        return x_response


@singleton
class TUserAccount(TTable):
    """ Read the registry for local users """
    def __init__(self):
        """ Setup the table """
        # noinspection PyArgumentList
        super().__init__(column_names=['User', 'SID'], title='Users')
        self.read_windows_registry()
        self.lock = Lock()

    def read_windows_registry(self):
        x_profile_list_hdl = None
        x_tcp_ip_hdl       = None
        x_profile_list_hdl = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, 'SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\ProfileList')
        for i in range(15):
            try:
                x_sid         = winreg.EnumKey(x_profile_list_hdl, i)
                x_profile_key = winreg.OpenKey(x_profile_list_hdl, x_sid)

                # pick only local users
                x_image_path  = winreg.QueryValueEx(x_profile_key, 'ProfileImagePath')
                x_sid_binary  = winreg.QueryValueEx(x_profile_key, 'Sid')
                x_sid_person  = struct.unpack('qi', x_sid_binary[0][:12])
                if x_sid_person[1] != 21:
                    continue

                x_parts = x_image_path[0].split(os.sep)
                x_user  = x_parts[-1]

                self.append([x_user, x_sid])
                # x_tcp_ip_hdl    = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, 'SYSTEM\\CurrentControlSet\\Services\\Tcpip\\Parameters')
                # x_hostname      = winreg.QueryValueEx(x_tcp_ip_hdl, 'hostname')
                # self.m_hostname = x_hostname[0]
            except (OSError, FileNotFoundError) as ex:
                # todo log message from exception
                continue
        if x_profile_list_hdl:
            x_profile_list_hdl.Close()

    def do_select(self, row_id: str) -> TTableRow | None:
        """ Overwrite do_select to be thread save
        :param row_id: The key to search for
        :return: The row with the row_id or None, if not found
        """
        with self.lock:
            return super().do_select(row_id)

    def get_selected_row(self) -> TTableRow | None:
        """ Access the results of previous do_select
        :return: The selected row
        """
        with self.lock:
            return self.selected_row


class TEezzyFreeScan(Thread):
    """ Threads scans bluetooth devices in range and TBluetooth tries to couple known addresses """
    def __init__(self):
        super().__init__(daemon=True, name='EEzzyFreeScan')

    def run(self) -> None:
        while True:
            TBluetooth().find_devices()
            time.sleep(2)


class TQueueRequest(Thread):
    """ Thread queues requests to bluetooth device. This allows to intermix the frequent automatic requests
    with user commands
    Usage: put a request to the request_queue and wait for answer in the response queue
    All requests have a timeout, so there will be no dead-lock with blocking access to the queues """
    def __init__(self, address: str):
        super().__init__(daemon=True, name='EezzyFreeLock')
        self.address        = address
        self.bt_service     = TBluetoothService(address)
        self.condition      = Condition()
        self.request_queue  = Queue()
        self.response_queue = Queue()
        self.eezzy_free     = False

    def run(self):
        if not self.bt_service.connected:
            return

        while True:
            try:
                x_req = self.request_queue.get(block=True, timeout=4)
                x_res = self.bt_service.send_request(x_req)
                x_res['address'] = self.address
                self.response_queue.put(x_res)
                self.response_queue.task_done()
            except queue.Empty:
                # Ping if the device is still in range and lock workstation, if eezzy-free is active
                if self.eezzy_free:
                    x_response = self.bt_service.send_request({'command': 'PING'})
                    if x_response['return']['code'] != 200 and x_response['return']['code'] != 100:
                        ctypes.windll.user32.LockWorkStation()
                        break


@dataclass(kw_only=True)
class TBluetoothService:
    """ The TBluetoothService handles a connection for a specific mobile given by bt_address """
    eezz_service: str = "07e30214-406b-11e3-8770-84a6c8168ea0"
    m_lock: Lock      = Lock()
    bt_socket         = None
    bt_service: list  = None
    bt_address        = None

    def __init__(self, address):
        self.connected  = False
        self.bt_address = address
        self.open_connection()

    def open_connection(self) -> bool:
        if self.connected:
            return True
        self.bt_service = bluetooth.find_service(uuid=self.eezz_service, address=self.bt_address)
        if self.bt_service:
            self.bt_socket  = BluetoothSocket(bluetooth.RFCOMM)
            self.bt_socket.connect((self.bt_socket[0]['host'], self.bt_socket[0]['port']))
            self.connected  = True
        return self.connected

    def send_request(self, message: dict) -> dict:
        if not self.open_connection():
            return {"return": {"code": 500, "value": f'Could not connect to EEZZ service on {self.bt_address}'}}

        try:
            with self.m_lock:
                x_timeout: float = 1.0
                # Send request: Wait for writer and send message
                x_rd, x_wr, x_err = select.select([], [self.bt_socket], [], x_timeout)
                if not x_wr:
                    return {"return": {"code": ReturnCode.TIMEOUT, "value": f'EEZZ service timeout on {self.bt_address}'}}

                for x_writer in x_wr:
                    x_writer.send(json.dumps(message).encode('utf8'))
                    break

                # receive an answer
                x_rd, x_wr, x_err = select.select([self.bt_socket], [], [self.bt_socket], x_timeout)
                for x_error in x_err:
                    return {"return": {"code": 514, "value": ''}}

                for x_reader in x_rd:
                    x_result   = x_reader.recv(1024)
                    x_result   = x_result.decode('utf8').split('\n')[-2]
                    return json.loads(x_result)

                return {"return": {"code": ReturnCode.TIMEOUT, "value": f'EEZZ service timeout on {self.bt_address}'}}
        except OSError as xEx:
            self.connected = False
            self.bt_socket.close()
            return {"return": {"code": 514, "value": xEx}}


# --- Section for module test
def test_user_table():
    """ Test access to the windows registry """
    x_users = TUserAccount()
    x_users.print()


def test_bluetooth_table():
    """ Test the access to the bluetooth environment """
    x_users = TBluetooth()
    with x_users.condition:
        x_users.condition.wait()
    x_users.print()


if __name__ == '__main__':
    """ Main entry point for module tests """
    test_user_table()
    test_bluetooth_table()
