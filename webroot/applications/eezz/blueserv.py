# -*- coding: utf-8 -*-
"""
    Copyright (C) 2015 www.EEZZ.biz (haftungsbeschränkt)

    TBluetooth:
    singleton to drive the bluetooth interface
    

"""
import inspect
from   pathlib import Path
import logging
import linecache
import os
import ctypes
import select
from   threading       import Thread, Lock, Condition
from   table           import TTable, TTableRow
from   service         import singleton

import bluetooth
from   bluetooth       import BluetoothSocket
import base64

from   Crypto          import Random
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
from   database         import TMobileDevices
from   dataclasses      import dataclass

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
        self.bt_service     = TBluetoothService()
        self.easy_free_lock_thread: Thread | None = None
        self.easy_free_scan_thread: Thread | None = None

        self.easy_free_scan_thread = TEezzyFreeScan()
        self.easy_free_scan_thread.start()
        self.couped_address = None

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
                self.do_eezzyfree(x_address)
            self.do_sort(1)

    def do_eezzyfree(self, address: str) -> None:
        """  Check if the connection to device already exists, which is ensured by a running eezzy-free thread, which
        sends frequently a PING request.
        :param address: The address to connect to if not already done
        """
        if not TMobileDevices().do_find('CAddr', address):
            return
        if not (self.easy_free_lock_thread and self.easy_free_lock_thread.is_alive()):
            self.couped_address = address
            self.easy_free_lock_thread = TEezzyFree(address)
            self.easy_free_lock_thread.start()

    def get_coupled_user(self, sid: str = None) -> dict | None:
        """
        :param sid: The SID to crosscheck the current connection
        :return: The password to unlock the workstation
        """
        if not self.couped_address:
            return {'result': {'code': 500}}

        x_row = TMobileDevices().get_couple(self.couped_address)
        # ['CAddr', 'CDevice', 'CSid', 'CUser', 'CAddr', 'CVector', 'CKey']
        x_addr, x_device, x_sid, x_user, x_sid, x_vector64, x_rsa_key = x_row.get_values()
        # todo check x_sid == sid

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

    def set_coupled_user(self, device_address: str, device_name: str, user: str, sid: str, password: str) -> dict:
        """ Stores the user password on mobile device
        """
        x_vector     = Random.new().read(16)
        x_vector64   = base64.b64encode(x_vector)
        x_pwd_encr   = b''.join([bytes([(int(x) ^ y) & 0xff]) for x, y in zip_longest(password.encode('utf8'), x_vector, fillvalue=0)])
        x_pwd_encr64 = base64.b64encode(x_pwd_encr).decode('utf8')
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
        x_response    = self.bt_service.send_request(device_address, {"command": "SETUSR", "args": [sid, x_pwd_encr64, x_pub_key_str]})

        # insert the public key into local database
        if x_response['return']['code'] == 200:
            # :address, :device, sid, :user, :vector, :key
            TMobileDevices().db_insert(row_data={'address': device_address, 'device': device_name, 'sid': sid, 'user': user, 'vector64': x_vector64, 'key': x_priv_key})
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
        TMobileDevices()
        if not email or not iban or not password or not alias:
            x_response = {"return": {"code": ReturnCode.ATTRIBUTES.value, "value": "mandatory fields missing"}}
            return x_response

        x_hash_md5 = MD5.new(password.encode('utf8'))
        x_address  = address
        x_request  = {"command": "Register",
                      "args":  [x_hash_md5.hexdigest(), ''],
                      "TUser": {"CEmail": email, "CNameAlias": alias, "CNameFirst": fname, "CNameLast": lname, "CIban": iban}}
        x_response = self.bt_service.send_request(x_address, x_request)
        x_response['address'] = x_address

        if x_response['return']['code'] == 200:
            # self.mDatabase.setSim(x_address, x_response['TDevice']['CSim'])
            x_response['return']['args'] = [_('Reply to verification E-Mail to accomplish registration')]
        return x_response

    def get_eezz_key(self):
        """ Get the private key from selected device """
        if self.couped_address == None:
            return {'result': {'code': 500, 'value': 'device out of range'}}
        x_response = self.bt_service.send_request(self.couped_address, {"command": "GETKEY", "args": []})
        x_response['address'] = self.couped_address
        return x_response

    def get_visible_rows(self, get_all=False) -> list:
        """ thread save access to the table entries
        :return: all table rows
        """
        with self.m_lock:
            return super().get_visible_rows(get_all=True)


@singleton
class TUserAccount(TTable):
    """  """
    def __init__(self, path: str):
        # noinspection PyArgumentList
        super().__init__(column_names=['User', 'SID'], title='Users')
        self.m_hostname = str()
        self.read_windows_registry()
        self.lock = Lock()

    def read_windows_registry(self):
        try:
            x_profile_list_hdl = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, 'SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\ProfileList')

            for i in range(15):
                x_sid         = winreg.EnumKey(x_profile_list_hdl, i)
                x_profile_key = winreg.OpenKey(x_profile_list_hdl, x_sid)

                # pick only local users
                x_image_path  = winreg.QueryValueEx(x_profile_key, 'ProfileImagePath')
                x_sid_binary  = winreg.QueryValueEx(x_profile_key, 'Sid')
                x_sid_person  = struct.unpack('qi', x_sid_binary[0][:12])
                if x_sid_person[1] != 21:
                    continue

                x_parts       = x_image_path[0].split(os.sep)
                x_user        = x_parts[-1]

                self.append([x_user, x_sid])
                x_tcp_ip_hdl    = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, 'SYSTEM\\CurrentControlSet\\Services\\Tcpip\\Parameters')
                x_hostname      = winreg.QueryValueEx(x_tcp_ip_hdl, 'hostname')
                self.m_hostname = x_hostname[0]
        except OSError:
            return

    def do_select(self, row_id: str) -> TTableRow | None:
        with self.lock:
            return super().do_select(row_id)

    def get_selected_row(self) -> TTableRow | None:
        with self.lock:
            return super().get_selected_row()


class TEezzyFreeScan(Thread):
    def __init__(self):
        super().__init__(daemon=True, name='EEzzyFreeScan')

    def run(self) -> None:
        while True:
            TBluetooth().find_devices()
            time.sleep(2)


class TEezzyFree(Thread):
    def __init__(self, address: str):
        super().__init__(daemon=True, name='EezzyFreeLock')
        self.m_address = address
        self.m_service = TBluetoothService()

    def run(self):
        while True:
            x_response = self.m_service.send_request(self.m_address, {'command': 'PING'})
            if x_response['return']['code'] != 200 and x_response['return']['code'] != 100:
                ctypes.windll.user32.LockWorkStation()
                TBluetooth().coupled_address = None
                break
            time.sleep(2)


@singleton
class TBluetoothService:
    def __init__(self):
        self.eezz_service = "07e30214-406b-11e3-8770-84a6c8168ea0"
        self.m_lock       = Lock()
        self.bt_socket    = None

    def send_request(self, address: str, message: dict) -> dict:
        try:
            with self.m_lock:
                # Connect to the selected bluetooth device
                x_service = bluetooth.find_service(uuid=self.eezz_service, address=address)
                x_socket  = BluetoothSocket(bluetooth.RFCOMM)
                x_timeout: float = 0.4

                if not x_service:
                    return {"return": {"code": ReturnCode.BT_SERVICE, "value": f'EEZZ service not acitve on {address}'}}
                x_socket.connect((x_service[0]['host'], x_service[0]['port']))

                # Send request: Wait for writer and send message
                x_rd, x_wr, x_err = select.select([], [x_socket], [x_socket], x_timeout)
                if not x_wr:
                    x_socket.close()
                    return {"return": {"code": ReturnCode.TIMEOUT, "value": f'EEZZ service timeout on {address}'}}

                for x_writer in x_wr:
                    x_writer.send(json.dumps(message).encode('utf8'))
                    break

                # receive an answer
                x_rd, x_wr, x_err = select.select([x_socket], [], [x_socket], x_timeout)
                for x_error in x_err:
                    x_error.close()
                    return {"return": {"code": 514, "value": ''}}

                for x_reader in x_rd:
                    x_result   = x_reader.recv(1024)
                    x_result   = x_result.decode('utf8').split('\n')[-2]
                    x_reader.close()
                    return json.loads(x_result)

                return {"return": {"code": ReturnCode.TIMEOUT, "value": f'EEZZ service timeout on {address}'}}
        except OSError as xEx:
            x_socket.close()
            return {"return": {"code": 514, "value": xEx}}


if __name__ == '__main__':
    """ Main entry point for mdule tests
    """
    frameinfo = inspect.getframeinfo(inspect.currentframe())
    x_path = Path(frameinfo.filename)

    print(f'File: {x_path.stem}, {frameinfo.lineno}')
    print(TMessage().message[1000])
    exit()

