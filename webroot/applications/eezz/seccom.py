# -*- coding: utf-8 -*-
"""
    Copyright (C) 2015 www.EEZZ.biz (haftungsbeschränkt)

    TSecureSocket: 
    Implements secure communication with eezz server
    using RSA and AES encryption
    
"""
import io, os
import urllib
import urllib.request
import base64
import struct
from   Crypto.PublicKey  import RSA
from   Crypto.Cipher     import PKCS1_v1_5
# from   Crypto.Signature import PKCS1_v1_5
from   Crypto.Hash       import SHA256, SHA
from   Crypto.Cipher     import AES
from   Crypto            import Random
import service


class TSecureSocket:
    """ """
    def __init__(self):
        pass
        
    def send_request(self, a_action, a_header = None, a_data = None):
        # AES encryption with vector and key of AES
        # xVector   = Random.new().read(AES.block_size)
        x_vector   = Random.new().read(AES.block_size)
        x_key      = Random.new().read(AES.block_size)
        
        x_hdr_req   = io.BytesIO()
        x_hdr_req.write(x_key)
        x_hdr_req.write(x_vector)
        
        if a_data:
            x_hdr_req.write(struct.pack('>H', len(a_data)))
            x_cipher   = AES.new(x_key, AES.MODE_CBC, x_vector)
            x_padding  = divmod(16 - divmod(len(a_data), 16)[1], 16)[1]
            x_req_body  = x_cipher.encrypt(a_data.encode('utf8') + x_padding * b'\x00')
        else:
            x_hdr_req.write(struct.pack('>H', 0))
            x_req_body  = b''
            
        x_hdr_req.write(struct.pack('>H', len(a_action)))
        x_hdr_req.write(a_action.encode('utf8'))
        
        if a_header:
            for xElem in a_header:
                if isinstance(xElem, bytes):
                    x_hdr_req.write(struct.pack('>H', len(xElem)))
                    x_hdr_req.write(xElem)
                elif isinstance(xElem, str):
                    x_hdr_req.write(struct.pack('>H', len(xElem)))
                    x_hdr_req.write(xElem.encode('utf8'))
        x_hdr_req.write(struct.pack('>H', 0))
            
        # RSA containing vector and key of AES
        x_rsa_key = service.TService().private_key
        x_chiper  = PKCS1_v1_5.new(x_rsa_key)
        x_req_hdr = x_chiper.encrypt(x_hdr_req.getvalue())
        
        # Compile the encrypted body request
        x_req_post  = io.BytesIO()
        x_req_post.write(x_req_hdr)
        x_req_post.write(x_req_body)
        x_request_url = urllib.request.urlopen('http://www.eezz.biz/eezz.php', base64.b64encode(x_req_post.getvalue()))
        
        x_response = io.BytesIO()
        x_response.write(x_request_url.read())
        x_index    = x_response.tell()
        x_response.truncate(divmod(x_index, 16)[0] * 16)
        x_response.seek(0)

        x_cipher    = AES.new(x_key, AES.MODE_CBC, x_vector)
        x_resp_body = x_cipher.decrypt(x_response.getvalue())
        
        try:
            x_index    = x_resp_body.index(b'\x00')
            x_resp_body = x_resp_body[:x_index]
        except ValueError:
            pass
        
        return x_resp_body


if __name__ == '__main__':
    os.chdir(os.path.join('/', 'Users', 'Paul', 'production', 'webroot', 'public'))
    aSecSock = TSecureSocket()
    aResp    = aSecSock.send_request('test', [12345678, 2345], 'some data')
    print(aResp)
    pass

