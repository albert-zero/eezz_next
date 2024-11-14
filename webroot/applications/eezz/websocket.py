"""
This module implements the following classes

    * :py:class:`eezz.websocket.TWebSocketAgent`:       The abstract class has to be implemented by the user to \
    drive the TWebSocketClient
    * :py:class:`eezz.websocket.TWebSocketException`:   The exception for errors on low level interface
    * :py:class:`eezz.websocket.TWebSocketClient`:      This class interacts with the TWebSocketAgent and HTML frontend
    * :py:class:`eezz.websocket.TWebSocket`:            Low level access to the socket interface
    * :py:class:`eezz.websocket.TAsyncHandler`:         This class is used to interact with user defined methods

The TWebSocket implements the protocol according to
`rfc 6455 <https://tools.ietf.org/html/rfc6455>`_

"""
import io
import struct
import socket
# from   Crypto.Hash import SHA
import hashlib
import base64
import time

import select
import json

from   abc         import abstractmethod
from   threading   import Thread, Lock
from   typing      import Any, Callable, Dict
from   service     import TService
from   loguru      import logger


class TWebSocketAgent:
    """ User has to implement this class to receive data.
    TWebSocketClient is called with the class type, leaving the TWebSocketClient to generate an instance
    """
    @abstractmethod
    def setup_download(self, request_data: dict) -> str:
        """ This method is called before a download of files starts """
        return ''

    @abstractmethod
    def handle_request(self, request_data: Any) -> str:
        """ handle request expects a json structure """
        return ''

    @abstractmethod
    def handle_download(self, description: str, raw_data: Any) -> str:
        """ handle download expects a json structure, describing the file and the data """
        return ''

    def shutdown(self):
        """ Implement shutdown to release allocated resources """
        pass


class TWebSocketException(Exception):
    """ Exception class for this module """
    def __init__(self, a_value):
        self.m_value = a_value

    def __str__(self):
        return repr(self.m_value)


class TWebSocketClient:
    """ Implements a WEB socket service thread. This class is created for each WebSocket connection

    :param a_client_addr:   The communication socket to the web-browser
    :type  a_client_addr:   Tuple[host, address]
    :param a_agent:         The agent class handle to handle incoming request
    :type  a_agent:         type[TWebSocketAgent]
    """
    def __init__(self, a_client_addr: tuple, a_agent: type[TWebSocketAgent]):
        self.m_headers      = None
        self.m_socket       = a_client_addr[0]
        self.m_cnt          = 0
        self.m_buffer       = None
        self.m_protocol     = str()
        self.m_agent_class  = a_agent
        self.m_agent_client = a_agent()
        self.m_lock         = Lock()
        self.m_threads: Dict[Callable, Thread] = {}

    def shutdown(self):
        # self.m_async.shutdown()
        pass

    def upgrade(self):
        """ Upgrade HTTP connection to WEB socket """
        logger.debug('establish web socket')
        x_bin_data = self.m_socket.recv(1024)
        if len(x_bin_data) == 0:
            raise TWebSocketException('no data received')

        x_utf_data = x_bin_data.decode('utf-8')
        x_response = self.gen_handshake(x_utf_data)
        x_nr_bytes = self.m_socket.send(x_response.encode('utf-8'))
        # self.m_agent_client = self.m_agent_class()
        self.m_buffer = bytearray(65536 * 2)

    def handle_request(self) -> None:
        """ Receives an request and send a response
        The given method is executed async, so there will be no blocking calls. After the call the result
        is collected.
        """
        x_json_str = self.read_websocket()
        x_json_obj = json.loads(x_json_str.decode('utf-8'))

        logger.debug(f'handle request {x_json_obj}')
        if 'download' in x_json_obj:
            x_response = self.m_agent_client.setup_download(x_json_obj)
            self.write_frame(x_response.encode('utf-8'))
            return

        if 'file' in x_json_obj:
            with self.m_lock:
                x_byte_stream = self.read_websocket()
                x_response    = self.m_agent_client.handle_download(x_json_obj, x_byte_stream)
                self.write_frame(x_response.encode('utf-8'))
            return

        if 'initialize' in x_json_obj:
            self.handle_aync_request(request=x_json_obj)
            return

        if 'call' in x_json_obj:
            x_request = x_json_obj['call']
            x_args    = x_request['args']
            x_name    = x_request['function']

            logger.debug(f'websocket call {x_name}( {x_args} )')
            x_obj, x_method, x_tag, x_descr = TService().get_method(x_request['id'], x_name)
            x_thread  = self.m_threads.get(x_method)

            # Wait for this method to terminate before launching a new request
            if x_thread and x_thread.is_alive():
                return

            x_descr   = f'{x_descr}.{x_name}'
            x_thread  = TAsyncHandler(socket_server=self, request=x_json_obj, method=x_method, args=x_args, description=x_descr)
            self.m_threads[x_method] = x_thread
            x_thread.start()
            # - x_response = self.m_agent_client.handle_request(x_json_obj)
            # - self.write_frame(x_response.encode('utf-8'))

    def handle_aync_request(self, request: dict) -> None:
        """ This method is called after each method call request by user interface. The idea of an async call is,
        that a user method is unpredictable long-lasting and could block the entire communication channel.
        The environment takes care, that the same method is not executed as long as prior execution lasts.

        :param request: The original request to execute after EEZZ function call
        :type  request: dict
        """
        with self.m_lock:
            x_response = self.m_agent_client.handle_request(request)
            self.write_frame(x_response.encode('utf-8'))

    def read_websocket(self) -> bytes:
        """ Read a chunk of data from stream

        :return: The chunk of data coming from browser
        """
        try:
            x_raw_data = bytes()
            while True:
                x_final, x_opcode, x_mask_vector, x_payload_len = self.read_frame_header()
                if x_opcode   == 0x8:
                    raise TWebSocketException("closed connection")
                elif x_opcode == 0x1:
                    x_raw_data += self.read_frame(x_opcode, x_mask_vector, x_payload_len)
                elif x_opcode == 0x2:
                    x_raw_data += self.read_frame(x_opcode, x_mask_vector, x_payload_len)
                elif x_opcode == 0x9:
                    x_utf_data = self.read_frame(x_opcode, x_mask_vector, x_payload_len)
                    self.write_frame(a_data=x_utf_data[:x_payload_len], a_opcode=0xA, a_final=(1 << 7))
                elif x_opcode == 0xA:
                    x_utf_data = self.read_frame(x_opcode, x_mask_vector, x_payload_len)
                    self.write_frame(a_data=x_utf_data[:x_payload_len], a_opcode=0x9, a_final=(1 << 7))
                else:
                    raise TWebSocketException(f"unknown opcode={x_opcode}")

                if x_final:
                    return x_raw_data
        except Exception as xEx:
            if self.m_agent_client:
                print("communication: connection closed: " + str(xEx))
                self.m_agent_client.shutdown()
                self.m_agent_client = None
            raise

    def gen_handshake(self, a_data: str):
        """ Upgrade HTTP connection to WEB-socket

        :param a_data: Upgrade request data
        :return:
        """
        x_key           = 'accept'
        x_lines         = a_data.splitlines()
        self.m_headers  = {x_key: x_val for x_key, x_val in [x.split(':', 1) for x in x_lines[1:] if ':' in x]}
        self.m_protocol = self.m_headers.get('Upgrade')
        
        if self.m_protocol != 'peezz':
            x_key = self.gen_key()

        x_handshake = io.StringIO()
        x_handshake.write('HTTP/1.1 101 Switching Protocols\r\n')
        x_handshake.write('Connection: Upgrade\r\n')
        x_handshake.write('Upgrade: websocket\r\n')
        x_handshake.write('Sec-WebSocket-Accept: {}\r\n'.format(x_key))
        x_handshake.write('\r\n')
        return x_handshake.getvalue()

    def gen_key(self):
        """ Generates a key to establish a secure connection

        :return: Base64 representation of the calculated hash
        """
        x_hash     = hashlib.sha1()
        x_64key    = self.m_headers.get('Sec-WebSocket-Key').strip()
        x_key      = x_64key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'
        x_hash.update(bytes(x_key, 'ascii'))
        return base64.b64encode(x_hash.digest()).decode('utf-8')

    def read_frame_header(self):
        """ Interpret the incoming data stream, starting with analysis of the first bytes

        :return: A tuple of all attributes, which enable the program to read the payload: \
        final(byte), opcode(data-type), mask(encryption), len(payload size)
        """
        x_bytes = self.m_socket.recv(2)
        
        if len(x_bytes) == 0:
            raise TWebSocketException('no data received')
                
        x_mask_vector = None
        x_final       = ((1 << 7) & x_bytes[0]) != 0
        x_opcode      = x_bytes[0] & 0xf
        x_masked      = ((1 << 7) & x_bytes[1]) != 0
        x_payload_len = int(x_bytes[1] & 0x7f)

        # calculate extended length
        if x_payload_len == 126:
            x_bytes = self.m_socket.recv(2)
            x_payload_len = struct.unpack('>H', x_bytes)[0]
        elif x_payload_len == 127:
            x_bytes = self.m_socket.recv(8)
            x_payload_len = struct.unpack('>Q', x_bytes)[0]

        # unpack data
        if x_masked:
            x_mask_vector = self.m_socket.recv(4)
        return x_final, x_opcode, x_mask_vector, x_payload_len

    def read_frame(self, x_opcode, a_mask_vector, a_payload_len):
        """ Read one frame

        :param x_opcode:        The opcode describes the data type
        :param a_mask_vector:   The mask is used to decrypt and encrypt the data stream
        :param a_payload_len:   The length of the data block
        :return:                The buffer with the data
        """
        if a_payload_len == 0:
            return bytearray()
        
        x_rest   = a_payload_len
        x_view   = memoryview(self.m_buffer)
        
        while x_rest > 0:
            x_num_bytes = self.m_socket.recv_into(x_view, x_rest)
            x_rest     = x_rest - x_num_bytes
            x_view     = x_view[x_num_bytes:]
                    
        if a_mask_vector:
            x_dimension = divmod((a_payload_len + 3), 4)
            x_view      = memoryview(self.m_buffer)
            
            x_int_size  = x_dimension[0]
            x_seq_mask  = struct.unpack('>I', bytearray(reversed(a_mask_vector)))[0]
            x_view_sli  = x_view[: x_int_size * 4]
            x_view_int  = x_view_sli.cast('I')
            
            # Calculate un-mask with int4
            for i in range(x_int_size):
                x_view_int[i] ^= x_seq_mask

            x_view_int.release()
            x_view_sli.release()
                                
        return self.m_buffer[:a_payload_len]

    def write_frame(self, a_data: bytes, a_opcode: hex = 0x1, a_final: hex = (1 << 7), a_mask_vector: list | None = None) -> None:
        """ Write single frame

        :param a_data:      Data to send to browser
        :type  a_data:      bytes
        :param a_opcode:    Opcode defines the kind of data
        :type  a_opcode:    byte
        :param a_final:     Indicates if all data are written to stream
        :type  a_final:     byte
        :param a_mask_vector: Mask to use for secure communication
        :type  a_mask_vector: List[byte,byte,byte,byte]
        """
        x_payload_len = len(a_data)
        x_bytes       = bytearray(10)
        x_position    = 0
        x_masked      = 0x0

        if a_mask_vector and len(a_mask_vector) == 4:
            x_masked  = 1 << 7

        x_bytes[x_position] = a_final | a_opcode
        x_position   += 1

        if x_payload_len > 126:
            if x_payload_len < 0xffff:
                x_bytes[x_position]  = 0x7E | x_masked
                x_position += 1
                x_bytes[x_position:x_position+2] = struct.pack('>H', x_payload_len)
                x_position += 2
            else:
                x_bytes[x_position]  = 0x7F | x_masked
                x_position += 1
                x_bytes[x_position:x_position+8] = struct.pack('>Q', x_payload_len)
                x_position += 8
        else:
            x_bytes[x_position] = x_payload_len | x_masked
            x_position += 1

        if x_masked:
            x_bytes[x_position:x_position+4] = a_mask_vector
            x_position += 4

        self.m_socket.send(x_bytes[0:x_position])
        if x_payload_len == 0:
            return

        if x_masked != 0:
            x_masked = bytearray(x_payload_len)
            for i in range(x_payload_len):
                x_masked[i] = a_data[i] ^ a_mask_vector[i % 4]
            self.m_socket.send(x_masked)
        else:
            self.m_socket.sendall(a_data)


class TWebSocket(Thread):
    """ Manage connections to the WEB socket interface.
    TWebSocket implements the socket of a http.server.HTTPServer

    :param a_web_address: The connection information
    :type  a_web_address: Tupel[host, address]
    :param a_agent_class: The implementation of the EEZZ protocol
    :type  a_agent_class: type[TWebSocketAgent]
    """
    def __init__(self, a_web_address: tuple, a_agent_class: type[TWebSocketAgent]):
        self.m_web_socket: socket = None
        self.m_web_addr    = a_web_address
        self.m_clients     = dict()
        self.m_agent_class = a_agent_class
        self.m_running     = True
        super().__init__(daemon=True, name='WebSocket')
    
    def shutdown(self):
        """ Shutdown closes all sockets """
        self.m_running = False
        for x_key, x_val in self.m_clients.items():
            x_key.close()
        self.m_web_socket.close()
        pass

    def run(self):
        """ Wait for incoming requests"""
        self.m_web_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.m_web_socket.bind((self.m_web_addr[0], self.m_web_addr[1]))
        self.m_web_socket.listen(15)

        x_read_list  = [self.m_web_socket]
        print(f'websocket {self.m_web_addr[0]} at {self.m_web_addr[1]}')

        while self.m_running:
            x_rd, x_wr, x_err = select.select(x_read_list, [], x_read_list, 1)
            if not x_rd and not x_wr and not x_err:
                continue
                            
            for x_socket in x_err:
                if x_socket is self.m_web_socket:
                    x_socket.close()
                    x_read_list.remove(x_socket)
                    print('server socket closed')
                    raise
                else:
                    x_read_list.remove(x_socket)
                    x_socket.close()
                    self.m_clients.pop(x_socket)

            for x_socket in x_rd:
                if x_socket is self.m_web_socket:
                    x_clt_addr  = self.m_web_socket.accept()
                    x_ws_client = TWebSocketClient(x_clt_addr, self.m_agent_class)
                    x_ws_client.upgrade()
                    self.m_clients[x_clt_addr[0]] = x_ws_client
                    x_read_list.append(x_clt_addr[0])
                else:
                    x_client: TWebSocketClient = self.m_clients.get(x_socket)
                    try:
                        x_client.handle_request()
                    except (TWebSocketException, ConnectionResetError, ConnectionAbortedError) as aEx:
                        x_client.shutdown()
                        x_socket.close()
                        x_read_list.remove(x_socket)
                        self.m_clients.pop(x_socket)


class TAsyncHandler(Thread):
    """ Execute method in background task.
    This class is designed to be put into an async thread to execute a user method, without blocking the websocket.
    After the method returns, the AsyncHandler creates the websocket response.
    It's also possible to specify a loop counter for successive calls to the same method. The loop count is
    given with the "_metha.loop" attribute. Some minimal time interval has to be calculated by the user method.
    This way you could implement a monitor measurement, sending actual data in some time intervals to the user interface.

    :param method:          The method to be executed
    :type  method:          Callable
    :param args:            The arguments for this method as key/value pairs plus meta-arguments with the reserved key\
    _meta, here with a loop request: ``{'_meta': {'loop': 100,...}}``. \
    The loop continues until the user method returns None
    :type  args:            Dict[name, value]
    :param socket_server:   The server to send the result
    :type  socket_server:   TWebSocketClient
    :param request:         The request, which is waiting for the method to return
    :type  request:         dict[eezz-lark-key:value]
    :param description:     The name of the thread
    """
    def __init__(self, method: Callable, args: dict, socket_server: TWebSocketClient, request: dict, description: str):
        super().__init__(daemon=True, name=description)
        self.method         = method
        self.args           = args
        self.socket_server  = socket_server
        self.request        = request

    def run(self):
        try:
            x_meta_args = self.args.pop('_meta')
            x_loop      = x_meta_args['loop'] if x_meta_args and 'loop' in x_meta_args else 1
        except KeyError:
            x_loop      = 1

        for i in range(x_loop):
            self.request['result'] = self.method(**self.args)
            self.socket_server.handle_aync_request(self.request)


# ---- Module test section:
def test_tcm():
    """ :meta private:
    simulate a time consuming method (tcm)"""
    for i in range(10):
        time.sleep(1)
        print('.', end='')
    print('')


class TestSocketServer(TWebSocketClient):
    """ :meta private:
    Simulate a request handler, waiting for a method to finish"""
    def handle_aync_request(self, request: dict):
        print(request)


def test_async_hadler():
    """ :meta private:
    Test for TAsyncHandler thread """
    print('Test TAsyncHandler: async method call and socket server output \n')
    x_req    = {'test': 'threads'}
    x_ss     = TestSocketServer(a_client_addr=('',), a_agent=TWebSocketAgent)
    x_thread = TAsyncHandler(method=test_tcm, args={}, socket_server=x_ss, request=x_req, description='test')
    x_thread.start()
    print('Main thread waiting for the method to return')
    x_thread.join()


if __name__ == '__main__':
    """:meta private:"""
    test_async_hadler()

