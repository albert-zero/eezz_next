import time
from dataclasses import dataclass
from blueserv    import TBluetooth, TBluetoothService
from threading   import Thread
from mobile      import TMobileDevices
from table       import TTable, TTableRow

from Crypto.PublicKey import RSA
from Crypto.Cipher    import PKCS1_v1_5
from Crypto           import Random

from itertools   import filterfalse
import base64
import winreg
import struct
import os
import json


@dataclass(kw_only=True)
class TSession(TTable):
    sid:                str     = None
    name:               str     = None
    desktop_connected:  bool    = False
    device_connected:   bool    = False
    paired_device:      TTableRow          | None = None
    bt_service:         TBluetoothService  | None = None
    bt_devices:         TBluetooth         = None
    mb_devices:         TMobileDevices     = None

    def __post_init__(self):
        self.column_names = ['User', 'SID']
        self.title        = 'Users'
        super().__post_init__()

        self.user_kw          = ['CEmail', 'CNameAlias', 'CNameFirst', 'CNameLast', 'CIban']
        self.mb_devices       = TMobileDevices()

    def handle_bt_devices(self):
        while True:
            # wait for bluetooth status and fetch all devices in range
            with self.bt_devices.bt_table_changed:
                self.bt_devices.bt_table_changed.wait()

            # Wait for desktop connection
            if not self.desktop_connected:
                break

            with self.bt_devices.bt_lock:
                x_my_paired_devices   = self.mb_devices.do_select(['CSid'], [self.sid])
                x_bt_devices_in_range = self.bt_devices.get_visible_rows()

                x_my_paired_addresses = [x['CAddress'] for x in x_my_paired_devices]
                x_bt_device_addresses = [x['Address']  for x in x_bt_devices_in_range]
                x_pairs  = [x for x in x_bt_device_addresses if x in x_my_paired_addresses]

                # Check if a connected device lost range
                if self.device_connected and self.paired_device['CAddress'] not in x_pairs:
                    self.bt_service.shutdown()
                    self.paired_device    = None
                    self.device_connected = False

                # Check if we could connect to a device in range
                if not self.device_connected and x_pairs:
                    self.bt_service       = TBluetoothService(address=x_pairs[0])
                    self.paired_device    = self.mb_devices.do_select(['CSid', 'CAddress'], [self.sid, x_pairs[0]])[0]
                    self.device_connected = True

    def connect(self, local_user: dict):
        if not self.desktop_connected:
            self.desktop_connected = True
            self.sid  = local_user['sid'][0]
            self.name = local_user['name'][0]

            self.append([self.sid, self.name], row_id=self.sid, exists_ok=True)
            x_handle_bluetooth = Thread(target=self.handle_bt_devices(), daemon=True, name='handle devices')
            x_handle_bluetooth.start()

    def send_request(self, command: str, args: list, user_data: list = None) -> dict:
        if not self.device_connected:
            return {}

        with self.bt_devices.bt_lock:
            x_command  = {"command": command, "args": args}
            if user_data:
                x_command['TUser'] = {x: y for x, y in zip(self.user_kw, user_data)}
            x_response = self.bt_service.send_request(message=x_command)
        return x_response

    def get_user_pwd(self) -> dict:
        """ Called by external process to unlock workstation

        :return: The password to unlock the workstation
        """
        if not self.device_connected:
            return {}

        x_address  = self.paired_device['CAddress']
        x_rsa_key  = self.paired_device['CKey']
        x_vector64 = self.paired_device['CVector']

        x_rsa_key = RSA.importKey(x_rsa_key)
        x_vector  = base64.b64decode(x_vector64)

        # Send password request to the device:
        x_response = self.send_request(command='GETUSR', args=[self.sid])
        if x_response['return']['code'] != 200:
            return {}

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
            x_response  = {"address": x_address, "return": {"code": 200, "value": "GETUSR", "args": [self.sid, x_pwd_clear]}}
            return x_response
        except Exception as x_ex:
            return {"return": {"code": 1120, "value": x_ex}}

    def pair_device(self, address: str,  password: str) -> dict:
        """ Stores the user password on mobile device. The password is encrypted and the key is stored in the
        windows registry. This method is called by the user interface
        - The user has to be connected to eezz, which is automatically done using the TaskBar tool
        - The address has to be selected via user interface

        :param address:
        :param password: Password will be encrypted and stored on device for unlock workstation
        :return: EEZZ Confirmation message as dict
        """
        with self.bt_devices.bt_lock:
            x_devices_to_pair  = self.bt_devices.do_select([], [address])[0]
            x_my_devices       = self.mb_devices.do_select(['CSid', 'CAddress'], [self.sid, address])[0]

        # Prepare update of password for existing pairing or create new
        if x_my_devices:
            x_rsa_key   = x_my_devices['CKey']
            x_vector    = x_my_devices['CVector']
        else:
            x_rsa_key   = RSA.generate(1024)
            x_vector    = Random.new().read(16)

        x_device_name   = x_devices_to_pair['Name']

        x_priv_key      = x_rsa_key.exportKey()
        x_vector64      = base64.b64encode(x_vector)
        x_pwd_encr      = b''.join([bytes([(int(x) ^ y) & 0xff]) for x, y in zip_longest(password.encode('utf8'), x_vector, fillvalue=0)])
        x_pwd_encr64    = base64.b64encode(x_pwd_encr).decode('utf8')

        try:
            # Store the base vector in registry as alternative retrieval of password
            x_device_hdl = winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, 'Software\\eezz\\assoc', 0, winreg.KEY_SET_VALUE)
            winreg.SetValueEx(x_device_hdl, self.sid, None, winreg.REG_BINARY, int.from_bytes(x_vector))
        except OSError as xEx:
            pass

        # Generate key for this couple
        # Public key is send to device, private key is stored on PC
        x_pub_key     = x_rsa_key.publickey().exportKey().decode('utf8')
        x_pub_key_str = ''.join([xLine for xLine in x_pub_key.split('\n') if not ('----' in xLine)])
        x_response    = self.send_request(command='SETUSR', args=[self.sid, x_pwd_encr64, x_pub_key_str])

        # insert the public key into local database
        if x_response['return']['code'] == 200:
            self.mb_devices.append([address, x_device_name, self.sid, self.name, x_vector64, x_priv_key])
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
        x_response = self.send_request(command='Register', args=[x_hash_md5.hexdigest(), ''], user_data=[email, alias, fname, iban])
        if x_response['return']['code'] == 200:
            # self.mDatabase.setSim(x_address, x_response['TDevice']['CSim'])
            x_response['return']['args'] = [_('Reply to verification E-Mail to accomplish registration')]
        return x_response

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

                self.append([x_user, x_sid], row_id=x_sid)
                # x_tcp_ip_hdl    = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, 'SYSTEM\\CurrentControlSet\\Services\\Tcpip\\Parameters')
                # x_hostname      = winreg.QueryValueEx(x_tcp_ip_hdl, 'hostname')
                # self.m_hostname = x_hostname[0]
            except (OSError, FileNotFoundError) as ex:
                # todo log message from exception
                continue
        if x_profile_list_hdl:
            x_profile_list_hdl.Close()
