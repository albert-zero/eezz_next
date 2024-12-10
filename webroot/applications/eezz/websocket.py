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
    def handle_request(self, request_data: Any) -> dict:
        """ handle request expects a json structure """
        return {}

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
    """
    The TWebSocketClient class handles the WebSocket client connection, transitioning a
    standard HTTP connection to a WebSocket, maintaining socket communication, and managing
    interaction with a specified WebSocket agent.

    This class is responsible for upgrading HTTP connections to WebSocket connections,
    handling requests sent over WebSocket, and managing asynchronous and synchronous
    processing of those requests. It interacts with a WebSocket agent class to delegate
    specific tasks, handle incoming data frames, generate handshake responses, and maintain
    the overall stability of the WebSocket communication channel.

    :ivar m_headers: Stores HTTP headers relevant for WebSocket handshake.
    :vartype m_headers: dict

    :ivar m_socket: The socket object related to the client connection.
    :vartype m_socket: Any

    :ivar m_cnt: A counter used internally (specific usage/context not documented).
    :vartype m_cnt: int

    :ivar m_buffer: A buffer space used for storing data received over the WebSocket.
    :vartype m_buffer: bytearray

    :ivar m_protocol: The protocol name used during WebSocket handshake.
    :vartype m_protocol: str

    :ivar m_agent_class: The class type of the WebSocket agent associated with this client.
    :vartype m_agent_class: type[TWebSocketAgent]

    :ivar m_agent_client: An instance of the WebSocket agent class, facilitating task delegation.
    :vartype m_agent_client: TWebSocketAgent

    :ivar m_lock: A threading lock used to ensure thread safety during operations.
    :vartype m_lock: Lock

    :ivar m_threads: A dictionary mapping asynchronous callables to their respective threads.
    :vartype m_threads: Dict[Callable, Thread]
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
        """
        Establishes a web socket connection by performing a handshake with the server.
        This method handles receiving initial binary data, decoding it to UTF-8, generating
        a handshake response, and sending this response back to establish a connection.
        It also initializes a buffer used for further communications.

        :raises TWebSocketException: if no data is received during the handshake process
        """
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
        """
        Handles incoming WebSocket requests by interpreting their JSON content and
        executing the appropriate method based on the included command. The method
        supports commands for 'download', 'file', 'initialize', and 'call'. The command
        determines which backend process or asynchronous task should be triggered.

        :return: None
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
                x_response    = self.m_agent_client.handle_download('x_json_obj', x_byte_stream)
                self.write_frame(x_response.encode('utf-8'))
            return

        if 'initialize' in x_json_obj:
            x_init_update: dict  = self.handle_async_request(request=x_json_obj)

            if x_init_update:
                for x_id, x_task_jsn in x_init_update.get('tasks'):
                    x_thread = TAsyncHandler(socket_server=self, id=x_id, request=x_task_jsn, do_loop=True)
                    x_thread.start()
            return

        if 'call' in x_json_obj:
            x_call_jsn  = x_json_obj['call']
            x_id        = x_call_jsn['id']

            logger.debug(f'websocket call {x_call_jsn["function"]}')
            x_thread  = TAsyncHandler(socket_server=self, id=x_id, request=x_json_obj, do_loop=False)
            x_thread.start()

    def handle_async_request(self, request: dict) -> dict:
        """
        Handles an asynchronous request by utilizing a client to process the
        request and subsequently sending the response. This function is
        intended to ensure thread safety when accessing shared resources.

        :param request:
            A dictionary containing the details of the request to be
            processed. It typically includes all necessary information
            required by the client for processing.
        :return:
            A string response generated by the client after handling the
            request. The response is also sent in an encoded format.
        """
        with self.m_lock:
            x_json_obj = self.m_agent_client.handle_request(request)
            x_response = json.dumps(x_json_obj)
            self.write_frame(x_response.encode('utf-8'))
            return x_json_obj

    def read_websocket(self) -> bytes:
        """
        Reads data from a websocket, processing various websocket frame opcodes such as
        text, binary, ping, and pong, until a final frame is encountered. Handles exceptions
        during the reading process, logging them and shutting down the client connection if necessary.

        :return: The raw bytes data read from the websocket until a final frame is encountered.

        :raises TWebSocketException: If a close frame is received or an unknown opcode is encountered.
        :raises Exception: For any other unexpected exceptions during the reading process.
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
                logger.info(f'{str(xEx)} : shutdown')
                self.m_agent_client.shutdown()
                self.m_agent_client = None
            raise

    def gen_handshake(self, a_data: str):
        """
        Generates a WebSocket handshake response based on the input request data. The
        function parses the request headers, determines the appropriate WebSocket
        protocol version and constructs the response necessary for the protocol
        switch. This is essential for establishing a connection that adheres to
        WebSocket protocol specifications.

        :param a_data: The raw HTTP request string containing headers that are used
                       to construct the WebSocket handshake response.
        :type a_data: str
        :return: A string representing the HTTP response for switching protocols,
                 formatted for a WebSocket handshake.
        :rtype: str
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
        """
        Generates a WebSocket accept key by concatenating the client's key with a
        GUID and hashing the result using SHA-1, followed by base64 encoding. This
        process is described in RFC 6455, Section 4.2.2, which is part of the
        WebSocket protocol specification. The key serves as a mechanism to ensure
        that the connection request is valid and not coming from a source that
        doesn't understand WebSockets, thereby providing a level of handshake
        security.

        :return: The WebSocket accept key, encoded in base64 format
        :rtype: str
        """
        x_hash     = hashlib.sha1()
        x_64key    = self.m_headers.get('Sec-WebSocket-Key').strip()
        x_key      = x_64key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'
        x_hash.update(bytes(x_key, 'ascii'))
        return base64.b64encode(x_hash.digest()).decode('utf-8')

    def read_frame_header(self):
        """
        Reads the header of a WebSocket frame from the socket, processing information
        related to control and payload data. This function extracts the fin bit, opcode,
        mask vector, and payload length from the frame's header, which are crucial for
        determining the frame's structure and for handling WebSocket connections
        in a compliant manner.

        :return: A tuple containing the following elements:
            - x_final (bool): Indicates if the frame is the final fragment.
            - x_opcode (int): Specifies the opcode defining the frame's content type.
            - x_mask_vector (bytes or None): The mask key if the payload is masked.
            - x_payload_len (int): Length of the payload data.
        :rtype: tuple
        :raises TWebSocketException: If no data is received from the socket.
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
        """
        Reads a frame from a socket and processes it based on the given opcode, mask vector,
        and payload length. If the payload length is zero, it returns immediately with an
        empty bytearray. Otherwise, it reads the payload from the socket into an internal buffer.

        If a mask vector is provided, it applies the mask to the payload using byte-wise
        XOR operations. The method supports both masked and unmasked frames typical of
        web socket communication.

        :param x_opcode: OpCode of the frame to be read, determining the type of frame.
        :type x_opcode: int, typically a WebSocket OpCode
        :param a_mask_vector: Mask vector for unmasking the frame's payload, if present.
        :type a_mask_vector: Optional[bytes], 4-byte sequence if present
        :param a_payload_len: Length of the payload that needs to be read from the socket.
        :type a_payload_len: int
        :return: A bytearray containing the unmasked payload of the read frame.
        :rtype: bytearray
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
        """
        Constructs and sends a WebSocket frame using the specified parameters. The function handles masking
        the payload if a mask vector is provided, maintains the frame structure as per the WebSocket protocol,
        and sends the frame through the established socket connection.

        :param a_data: The payload data to be sent in the WebSocket frame.
        :type a_data: bytes
        :param a_opcode: The opcode for the frame, indicating the type of data being sent (e.g., text, binary).
        :type a_opcode: hex, optional
        :param a_final: A flag indicating if this is the final fragment in a message. Defaults to 1 << 7.
        :type a_final: hex, optional
        :param a_mask_vector: A list of four byte mask keys used for masking the payload data.
                              If None, no masking is applied. Must be exactly 4 bytes if provided.
        :type a_mask_vector: list or None, optional
        :return: None
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
    """
    TWebSocket is a thread-based server for handling WebSocket connections.

    This class provides the implementation for a WebSocket server that
    listens for incoming WebSocket requests on a specified address and port.
    It uses a separate client handler class to manage the communication with
    each connected client and supports gracefully shutting down all sockets
    when required. The server runs as a daemon thread, allowing it to operate
    independently of the main application flow.

    :ivar m_web_socket: The main server socket for accepting client connections.
    :type m_web_socket: socket.socket
    :ivar m_web_addr: The tuple containing the IP address and port where the server listens.
    :type m_web_addr: tuple
    :ivar m_clients: A dictionary mapping client sockets to their handler instances.
    :type m_clients: dict
    :ivar m_agent_class: The class type used for creating client agent instances.
    :type m_agent_class: type[TWebSocketAgent]
    :ivar m_running: A boolean flag indicating if the server is currently active and accepting connections.
    :type m_running: bool
    """
    def __init__(self, a_web_address: tuple, a_agent_class: type[TWebSocketAgent]):
        self.m_web_socket: socket.socket | None  = None
        self.m_web_addr:    tuple = a_web_address
        self.m_clients:     dict  = dict()
        self.m_agent_class: type[TWebSocketAgent] = a_agent_class
        self.m_running:     bool  = True
        super().__init__(daemon=True, name='WebSocket')
    
    def shutdown(self):
        """ Shutdown closes all sockets """
        self.m_running = False
        for x_key, x_val in self.m_clients.items():
            x_key.close()
        self.m_web_socket.close()
        pass

    def run(self):
        """
        Establishes a WebSocket server that listens for incoming connections and
        handles client requests. The server operates in a loop where it waits for
        socket events, manages client connections, and processes incoming WebSocket
        messages. It handles errors by shutting down faulty connections and cleaning
        up resources appropriately.

        :raises: Exception if the server socket encounters an error
        """
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
                        logger.info(f'shutdown connection: {aEx}')


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
    def __init__(self, socket_server: TWebSocketClient, id: str, request: dict, do_loop: bool = False):
        self.method_name = request['call']['function']
        self.method_args = request['call']['args']
        self.id          = id
        x_obj, x_method, x_tag, x_descr = TService().get_method(id, self.method_name)
        super().__init__(daemon = True, name = x_descr)

        self.method         = x_method
        self.socket_server  = socket_server
        self.request        = request
        self.running: bool  = True
        self.do_loop: bool  = do_loop

    @logger.catch(reraise=True)
    def run(self):
        """
        Executes the request in either asynchronous or synchronous mode based on
        the request parameters. Continuously handles asynchronous requests while
        the 'running' attribute is True. In synchronous mode, processes the
        request once and returns the result.

        :return: None
        """
        while self.running:
            self.running = self.do_loop
            x_row        = self.method(**self.method_args) if self.method_args else self.method()
            self.request['result-value'] = ''
            self.request['result-type']  = ''

            # execute value request:
            if self.request.get('update'):
                for x_key, x_value in self.request['update'].items():
                    if isinstance(x_value, dict):
                        x_id     = self.id
                        x_object, x_method, x_tag, x_descr = TService().get_method(x_id, x_value['function'])
                        x_args   = {x_key: x_val.format(row=x_row) for x_key, x_val in x_value['args'].items()} if x_value.get('args') else {}
                        x_result = x_method(**x_args) if x_args else x_method()
                        self.request['result-value'] = {'target': x_key, 'type': 'base64', 'value': base64.b64encode(x_result).decode('utf8')}
                    elif not x_value.startswith('this'):
                        self.request['result-value'] = {'target': x_key, 'type': 'text',   'value': x_value.format(row=x_row)}

            self.request['result'] = x_row
            self.socket_server.handle_async_request(self.request)


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

