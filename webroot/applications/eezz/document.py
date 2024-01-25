"""
    * **TManifest**:   Document header representation.
    * **TDocuments**:  Document management

"""
import time
import tarfile
import json
import uuid
import base64
import logging

from io               import BufferedReader, BytesIO
from queue            import Queue
from filesrv          import TEezzFile, TFile, TFileMode
from service          import TService, TGlobal
from database         import TDatabaseTable
from pathlib          import Path
from seccom           import TSecureSocket
from session          import TSession

from threading        import Thread
from dataclasses      import dataclass
from typing           import List, Any, Dict
from Crypto           import Random
from Crypto.Cipher    import AES
from Crypto.Signature import PKCS1_v1_5
from Crypto.Hash      import SHA1, SHA256


@dataclass(kw_only=True)
class TManifest:
    """ The manifest represents the information for the EEZZ document. This structure is stored
    together with all other EEZZ files.

    :param author:
    :param price:
    :param currency:
    :param files:
    :param descr_document:
    :param named_values:
    :param column_names:
    """
    author:         str   = None
    """:meta private:"""
    price:          float = None
    """:meta private:"""
    currency:       str   = None
    """:meta private:"""
    files:          dict  = None
    """:meta private:"""
    descr_document: dict  = None
    """:meta private:"""
    named_values:   str   = None
    """:meta private:"""
    column_names:   List[str] = None
    """:meta private:"""

    def __post_init__(self):
        # Prepare consistent access of manifest to database
        x_map_names = [('CID',     'document_id'),
                       ('CKey',    'document_key'),
                       ('CAddr',   'address'),
                       ('CTitle',  'title'),
                       ('CDescr',  'descr'),
                       ('CLink',   'link'),
                       ('CStatus', 'status'),
                       ('CAuthor', 'author')]

        self.keys_sections       = ['document', 'signature', 'files']
        self.column_names        = [x[0] for x in x_map_names]
        self.keys_section_header = [x[1] for x in x_map_names] + ['price', 'currency', 'files']
        self.keys_section_files  = ['name', 'hash', 'hash_list', 'size', 'chunk_size', 'type']

        self.descr_header   = {x: '' if x != 'files' else {} for x in self.keys_section_header}
        self.descr_files    = {x: '' for x in self.keys_section_files}
        self.descr_document = {'document': self.descr_header, 'signature': ''}

    def set_values(self, section: str, proposed: dict | bytes):
        """ Insert values to the manifest. There is a restricted set of keys in a fixed structure

        :param section:     Define a section to add info
        :param proposed:    Proposed data: The program will pick only allowed keys
        :type  proposed:    dict
        """
        try:
            if section == 'document':
                self.descr_document['document'].update({x: y for x, y in proposed.items() if x in self.keys_section_header})
            elif section == 'signature':
                self.descr_document['signature'] = base64.b64encode(proposed).decode('utf-8')
            elif section == 'files':
                x_file_descr = {x: y for x, y in proposed.items() if x in self.keys_section_files and x != 'name'}
                self.descr_document['document']['files'].update({proposed['name']: x_file_descr})
        except (TypeError, KeyError):
            pass

    def scan(self, header: dict):
        self.descr_document['signature'] = header.get('signature')
        self.set_values('document', header['document'])
        for x in header['document']['files']:
            self.set_values('files', x)

    def __str__(self):
        return json.dumps(self.descr_document, indent=4)


@dataclass(kw_only=True)
class TDocuments(TDatabaseTable):
    """ Manages documents

    :param files_list:      List of files
    :param key:             Document key
    :param vector:          Document vector
    :param description:     Document description
    :param manifest:        Manifest as document header
    :type  manifest:        TManifest
    :param eezz_path:       Document file name
    :param name:            Document name
    """
    files_list:   List[TEezzFile] = None
    """:meta private:"""
    key:          bytes           = None
    """:meta private:"""
    vector:       bytes           = None
    """:meta private:"""
    description:  dict            = None
    """:meta private:"""
    manifest:     TManifest       = None
    """:meta private:"""
    eezz_path:    Path            = None
    """:meta private:"""
    name:         str             = None
    """:meta private:"""

    def __post_init__(self):
        self.manifest           = TManifest()
        self.map_files: Dict[str, TEezzFile] = dict()
        self.title              = 'TDocuments'
        self.column_names       = self.manifest.column_names
        self.session: TSession  = TGlobal.get_instance(TSession)

        super().__post_init__()

        for i, x in enumerate(self.column_descr):
            x.type  = 'text'
            x.alias = self.manifest.keys_section_header[i]

        for x in range(2):
            self.column_descr[x].options     = 'not null'
            self.column_descr[x].primary_key = True

        super().prepare_statements()
        super().db_create()

    def prepare_download(self, request: dict):
        """ Prepares the download of several files to include to an EEZZ document.
        The preparation puts all file descriptors into a queue and waits to all documents until the last download.
        This triggers the creation of the EEZZ document

        :param request:     The json format of a WEB socket request
        """
        self.description = request
        x_name           = request.get('name')
        x_files_descr    = request.get('files')
        x_queue          = Queue()
        # create empty files list_files TFile(... queue-put-on-ready)
        # store document data
        # create threads for each file: queue-block-on-get
        self.key         = Random.new().read(AES.block_size)
        self.vector      = Random.new().read(AES.block_size)
        x_path           = TService().document_path / base64.b64encode(self.key).decode('utf8')
        x_path.mkdir(exist_ok=True)

        self.manifest.set_values(section='document', proposed={'document_id':  uuid.uuid4()})
        self.manifest.set_values(section='document', proposed={'document_key': self.encrypt_key(self.key, self.vector)})
        self.manifest.set_values(section='document', proposed=request)

        for x in x_files_descr:
            x_destination = x_path / f'{x["name"]}.crypt' if x['type'] == 'crypt' else x_path / x['name']
            x_file = TEezzFile(key=self.key, vector=self.vector, response=x_queue, file_type=x['type'], chunk_size=x['chunk_size'], destination=x_destination, size=x['size'])
            self.map_files[x['name']] = x_file
            self.files_list.append(x_file)
        Thread(target=self.create_document, daemon=True, args=(x_name, len(x_files_descr), x_queue)).start()

    def handle_download(self, request: dict, raw_data: Any) -> dict:
        """ Handle file download

        :param request:     Download reqeust
        :param raw_data:    Data chunk to write
        :return:            Update response
        """
        x_sequence_nr   = request.get('sequence_nr')
        x_file          = self.map_files[request.get('name')]
        x_file.write(raw_data, x_sequence_nr, mode=TFileMode.ENCRYPT)
        request['update']['value'] = 100.0 * (x_file.transferred / x_file.size)
        return request['update']

    def create_document(self, name: str, nr_files: int, queue: Queue[TEezzFile]) -> None:
        """ After all files downloaded, The document header is registered on eezz server and signed
        All files are zipped together with this header.

        :param name:        Name of the document on disk
        :param nr_files:    Number of files in the queue
        :param queue:       The process queue
        """
        x_files  = list()
        for x in range(nr_files):
            x_file        = queue.get(block=True)
            x_hash        = SHA256.new()

            list(map(lambda x_segment: x_hash.update(x_segment), x_file.hash_chain))
            x_hash_digest = base64.b64encode(x_hash.digest()).decode('utf-8')
            self.manifest.set_values(section='files',
                                     proposed={'name':       x_file.destination.name,
                                               'hash_chain': x_file.hash_chain,
                                               'hash':       x_hash_digest,
                                               'chunk_size': x_file.chunk_size,
                                               'size':       x_file.size,
                                               'type':       x_file.file_type})
            x_files.append(x_file)

        # Store document header on EEZZ and sign the header
        x_document_id   = self.manifest.descr_document['document']['document_id']
        x_header        = self.manifest.descr_document['document']
        x_response      = self.register_document(x_document_id, x_header)
        if x_response['result']['code'] != 200:
            return

        # Create local EEZZ file using the signed header
        x_signed_header = json.loads(x_response['result']['value'])
        self.zip(manifest=x_signed_header, files=x_files)

        # Store the document header data to database
        x_row_data      = [self.manifest.descr_document['document'][x.alias] for x in self.column_descr]
        self.append(table_row=x_row_data)

    def get_document_header(self, name: str) -> dict:
        """ If a customer finds an EEZZ document, this method opens the zipped content and verifies the header.
        With the verified header, the document could be unzipped.

        :param name:
        :return:
        """
        x_manifest  = self.unzip(manifest_only=True)
        x_header    = base64.b64decode(x_manifest['document'])
        x_signature = base64.b64decode(x_manifest['signature'])
        x_hash      = SHA1.new(x_header)

        x_public_key = TService().public_key
        x_verifier   = PKCS1_v1_5.new(x_public_key)

        if not x_verifier.verify(x_hash, x_signature):
            return {'return': {'code': 530, 'value': 'Signature verification failed'}}

        x_response  = json.loads(x_header.decode('utf-8'))
        x_response['return']  = {'code': 200, 'value': 'OK'}
        return x_response

    def zip(self, manifest: dict, files: List[TFile]):
        x_zip_stream = BytesIO()
        x_zip_stream.write(json.dumps(manifest).encode('utf-8'))
        x_zip_root   = Path(self.eezz_path.stem)

        x_file_list = list()
        for x_file in files:
            x_file_list.append({'name':     x_file.destination.stem,
                                'type':     x_file.file_type,
                                'path':     x_zip_root,
                                'size':     x_file.size,
                                'chunk_size': x_file.chunk_size,
                                'hash':     x_file.hash_chain})

        with tarfile.TarFile(self.eezz_path, "w") as x_zip_file:
            # store the info at the start of the tar file
            x_entry_path       = x_zip_root / 'Manifest'

            x_tar_info         = tarfile.TarInfo(name=str(x_entry_path))
            x_tar_info.size    = x_zip_stream.tell()
            x_tar_info.mtime   = time.time()

            x_zip_stream.seek(0)
            x_zip_file.addfile(tarinfo=x_tar_info, fileobj=x_zip_stream)

            # store preview
            for x_file in files:
                x_entry_path     = x_zip_root / x_file.destination.name
                x_source_path    = x_file.destination
                x_stat_info      = x_source_path.stat()

                x_tar_info       = tarfile.TarInfo(name=str(x_entry_path))
                x_tar_info.size  = x_stat_info.st_size
                x_tar_info.mtime = time.time()

                with x_source_path.open("rb") as x_input:
                    x_zip_file.addfile(tarinfo=x_tar_info, fileobj=x_input)

    def unzip(self, manifest_only=False, document_key: bytes = None) -> dict:
        """ Unzip a file and return the Manifest in JSON format. Unzip needs the document key. If the key is not
        available, unzip the preview and the manifest, store the result into database for further processing

        :param manifest_only: Extract the header
        :param document_key: If set, try to decrypt the document on the fly
        :return: The header
        """
        x_manifest = dict()
        with tarfile.TarFile(self.eezz_path, "r") as x_zip_file:
            for x_tar_info in x_zip_file.getmembers():
                x_dest   = Path(x_tar_info.name)
                x_dest.mkdir(exist_ok=True)

                x_buffer: BufferedReader | None = x_zip_file.extractfile(x_tar_info)
                if x_dest.name == 'Manifest':
                    try:
                        x_manifest = json.loads(x_buffer.read().decode('utf8'))
                        if manifest_only:
                            return x_manifest
                    except json.decoder.JSONDecodeError:
                        logging.error(msg="Manifest not in JSON format", stack_info=True, stacklevel=3)
                    continue

                # Extract files only with correct key
                x_file_descr  = x_manifest['files'][x_tar_info.name]
                if not document_key and x_file_descr['type'] == 'main':
                    continue

                x_destination = x_dest.with_suffix('') if x_file_descr['type'] == 'main' else x_dest
                x_eezz_file   = TEezzFile(file_type   = x_file_descr['type'],
                                          destination = x_destination,
                                          size        = x_file_descr['size'],
                                          chunk_size  = x_file_descr['chunk_size'],
                                          key         = document_key[16:],
                                          vector      = document_key[:16],
                                          response    = Queue())
                x_eezz_file.read(x_buffer, x_file_descr['hash_list'])
        return x_manifest

    def register_document(self, document_key: bytes, document_header: dict) -> dict:
        """ Register document

        :param document_key:    Document key
        :param document_header: Document header
        :return:
        """
        # Store and sign the document header using the document-key and the device-sim
        x_eezz_connection   = TSecureSocket()
        x_response          = x_eezz_connection.send_request('reqheader', ['', document_key], document_header)
        x_json_response     = json.loads(x_response.decode('utf-8'))
        return x_json_response

    def buy_document(self, transaction_key: bytes):
        x_eezz_connection   = TSecureSocket()
        x_response          = x_eezz_connection.send_request('reqkeycommit', [transaction_key])
        x_json_response     = json.loads(x_response.decode('utf-8'))
        x_encrypted_key     = x_json_response['key']
        x_document_key      = self.decrypt_key(x_encrypted_key)
        x_json_response['return'] = {'code': 200, 'value': x_document_key}
        return x_json_response

    def get_document_info(self, document_id: bytes, buy_request=False) -> dict:
        x_device_sim        = ''
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

    def add_document(self, document_id: bytes, document_key: bytes) -> dict:
        x_response = self.session.send_request(command='ADDDOC', args=[document_id, document_key])
        return x_response

    def decrypt_key(self, encrypted_key: bytes) -> (bytes, bytes):
        """ Decrypt the document key

        :param encrypted_key: The encrypted key
        :return:              The decrypted key, vector pair
        """
        # Get the device key for decryption
        x_response          = self.session.send_request(command='GETKEY', args=[])
        x_dev_keyvector64   = x_response['return']['value']
        x_dev_keyvector     = base64.b64decode(x_dev_keyvector64)

        x_encrypted_key_64  = encrypted_key
        x_encrypted_key     = base64.b64decode(x_encrypted_key_64)

        x_cipher            = AES.new(x_dev_keyvector[16:], AES.MODE_CBC, x_dev_keyvector[:16])
        x_doc_keyvector     = x_cipher.decrypt(x_encrypted_key)
        return x_doc_keyvector[16:], x_dev_keyvector[:16]

    def encrypt_key(self, key: bytes, vector: bytes) -> bytes:
        """ Encrypt the document key

        :param key:     Document key
        :param vector:  Document vector
        :return:        encrypted document key
        """
        # Get the device key for encryption
        x_response          = self.session.send_request(command='GETKEY', args=[])
        x_dev_keyvector64   = x_response['return']['value']
        x_dev_keyvector     = base64.b64decode(x_dev_keyvector64)

        x_doc_keyvector     = base64.b64decode(key + vector)
        x_cipher            = AES.new(x_dev_keyvector[16:], AES.MODE_CBC, x_dev_keyvector[:16])
        x_encrypted_key     = x_cipher.encrypt(x_doc_keyvector)
        return x_encrypted_key


# -- section for module tests
def test_manifest():
    """:meta private:"""
    logging.debug(msg='Test class TManifest')
    x_manifest = TManifest()
    x_manifest.set_values(section='document', proposed={'author': 'Albert', 'price': 14.5, 'currency': 'EUR'})
    x_manifest.set_values(section='files',   proposed={'size': 1000, 'chunk_size': 1024, 'name': 'test.doc'})
    x_manifest.set_values(section='files',   proposed={'size': 2000, 'chunk_size': 1024, 'name': 'preview.doc'})
    logging.debug(msg=str(x_manifest))


if __name__ == '__main__':
    """:meta private:"""
    x_service  = TService(root_path=Path(r'C:\Users\alzer\Projects\github\eezz_full\webroot'))
    x_log_path = x_service.logging_path / 'app.log'
    logging.basicConfig(filename=x_log_path, filemode='w', style='{', format='{name} - {levelname} - {message}')
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)

    my_doc = TDocuments()

