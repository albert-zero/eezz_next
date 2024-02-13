"""
    * **TManifest**:   Document header representation.
    * **TDocuments**:  Document management

"""
import time
import tarfile
import json
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
from Crypto.Signature import pkcs1_15
from Crypto.Hash      import SHA1, SHA256, SHA384


@dataclass(kw_only=True)
class TManifest:
    """ The manifest represents the information for the EEZZ document. This structure is stored
    together with all other EEZZ files.

    :param column_names:
    :param document_key:
    """
    document_key:   bytes     = None
    """:meta private:"""
    column_names:   List[str] = None
    """:meta private:"""

    def __post_init__(self):
        # Prepare consistent access of manifest to database
        x_map_names = [('CKey',      'document_key'),
                       ('CAddr',     'address'),
                       ('CTitle',    'title'),
                       ('CDescr',    'descr'),
                       ('CLink',     'link'),
                       ('CStatus',   'status'),
                       ('CAuthor',   'author'),
                       ('CPrice',    'price'),
                       ('CCurrency', 'currency')]

        self.column_names        = [x[0] for x in x_map_names]

        self.keys_sections       = ['document', 'files']
        self.keys_section_header = [x[1] for x in x_map_names] + ['files']
        self.keys_section_files  = ['name', 'hash', 'hash_list', 'size', 'chunk_size', 'type']

        self.descr_header   = {x: '' if x != 'files' else {} for x in self.keys_section_header}
        self.descr_files    = {x: '' for x in self.keys_section_files}
        self.descr_document = {'document': self.descr_header, 'signature': ''}

    def get_key(self) -> str:
        return self.descr_document['document']['document_key']

    def get_values(self) -> list:
        return [self.descr_document['document'][x[1]] for x in zip(self.column_names, self.keys_section_header)]

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

    def scan(self, header: str):
        x_manifest  = json.loads(header)

        x_manifest['document']['document_key'] = x_manifest['document']['guid']
        self.set_values('document',  x_manifest['document'])
        for x in x_manifest['files']:
            self.set_values('files',  x_manifest[x])

    def __str__(self):
        return json.dumps(self.descr_document, indent=4)


@dataclass(kw_only=True)
class TDocuments(TDatabaseTable):
    """ Manages documents
    There are two ways to start the document:
    
    1. Create a document using prepare_download, handle_download and create
       As a result the document is zipped in TAR format with a signed manifest. The key for decryption is stored on
       the mobile device and on EEZZ
    
    2. Open a document, reading the manifest. Noe you could check if you have the key on your mobile device, or you
       buy the key from EEZZ

    :param files_list:      List of files
    :param key:             Document key
    :param vector:          Document vector
    :param description:     Document description
    :param manifest:        Manifest as document header
    :type  manifest:        TManifest
    :param eezz_path:       Document file name
    :param name:            Document name
    """
    header:       dict            = None
    """:meta private:"""
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

        # Primary key
        for x in range(1):
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

        # Create a new document structure and pick values for the manifest
        self.manifest = TManifest(document_key=self.key + self.vector)
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
        response = self.eezz_register_document()
        if response['result']['code'] != 200:
            return

        # Create local EEZZ file using the signed header
        x_signed_header = response
        x_signed_header.pop('result')

        self.zip(destination=Path(name), manifest=x_signed_header, files=x_files)

        # Store the document header data to database
        x_row_data = [self.manifest.descr_document['document'][x.alias] for x in self.column_descr]
        self.append(table_row=x_row_data)

    def read_document_header(self, source: Path) -> bool:
        """ If a customer finds an EEZZ document, this method opens the zipped content and verifies the header.
        With the verified header, the document could be unzipped.

        :param source:
        :return:
        """
        x_manifest  = self.unzip(source=source, manifest_only=True)
        x_header    = base64.b64decode(x_manifest['document'])
        x_signature = base64.b64decode(x_manifest['signature'])
        x_hash      = SHA1.new(x_header)

        x_public_key = TService().public_key
        x_verifier   = pkcs1_15.new(x_public_key)

        if not x_verifier.verify(x_hash, x_signature):
            return False

        self.manifest = TManifest()
        self.manifest.scan(x_header.decode('utf8'))
        return True

    def zip(self, destination: Path, manifest: dict, files: List[TFile]):
        """ Zip the given files and the manifest to an EEZZ document.

        :param destination: Path to the EEZZ document. Has to be like <directory>/<filename>
        :param manifest:    Description and header
        :param files:       Files included for the document
        """
        x_zip_stream = BytesIO()
        x_zip_stream.write(json.dumps(manifest).encode('utf-8'))
        x_zip_root   = destination.parent

        with tarfile.TarFile(destination, "w") as x_zip_file:
            # store the info at the start of the tar file
            x_entry_path       = Path(x_zip_root) / 'Manifest'

            x_tar_info         = tarfile.TarInfo(name=str(x_entry_path))
            x_tar_info.size    = x_zip_stream.tell()
            x_tar_info.mtime   = time.time()

            x_zip_stream.seek(0)
            x_zip_file.addfile(tarinfo=x_tar_info, fileobj=x_zip_stream)

            # store preview
            for x_file in files:
                x_entry_path     = Path(x_zip_root) / x_file.destination.name
                x_source_path    = x_file.destination
                x_stat_info      = x_source_path.stat()

                x_tar_info       = tarfile.TarInfo(name=str(x_entry_path))
                x_tar_info.size  = x_stat_info.st_size
                x_tar_info.mtime = time.time()

                with x_source_path.open("rb") as x_input:
                    x_zip_file.addfile(tarinfo=x_tar_info, fileobj=x_input)

    def unzip(self, source: Path, manifest_only=False, document_key: bytes = None) -> dict:
        """ Unzip a file and return the Manifest in JSON format. Unzip needs the document key. If the key is not
        available, unzip the preview and the manifest, store the result into database for further processing

        :param source:
        :param manifest_only: Extract the header
        :param document_key: If set, try to decrypt the document on the fly
        :return: The header
        """
        x_manifest = dict()
        with tarfile.TarFile(source, "r") as x_zip_file:
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

                # Extract files only with correct key.
                # For an owner, the document key is stored in database. First extract the manifest and continue
                # with access on database....
                # To get ownership, proceed buy process for document key
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

    def eezz_register_document(self) -> dict:
        """ Stores header and return signature
        insert into TDocument (CID, RUser, CStatus, CHashDoc, CKey, CDiscount, CPrice, CCurrency, CTitle)

        """
        # Store and sign the document header using the document-key
        x_eezz_connection   = TSecureSocket()
        x_response          = x_eezz_connection.send_request('reqheader', [self.session.sid, self.manifest.get_key()], str(self.manifest))
        x_json_response     = json.loads(x_response.decode('utf-8'))
        return x_json_response

    def eezz_buy_document(self, transaction_key: bytes):
        """ From eezz_get_document_header you received a transaction key. You have to sign this key and send a bzy
        request. This ensures, that the registered device is in range and the device key is accessible.

        :param transaction_key:
        """
        # x_key    = self.get_device_key()
        # RsaKey
        # x_signer = pkcs1_15.new(x_key)
        # x_hash   = SHA256.new(transaction_key)
        # x_sign   = x_signer.sign(x_hash)

        x_eezz_connection   = TSecureSocket()
        x_response          = x_eezz_connection.send_request('reqkeycommit', [transaction_key])
        x_json_response     = json.loads(x_response.decode('utf-8'))
        x_encrypted_key     = x_json_response['key']
        key_vector          = self.decrypt_key_with_device(x_encrypted_key)
        self.manifest.set_values('document',  {'document_key': key_vector})
        self.append(self.manifest.get_values())

    def eezz_get_document_key(self, buy_request=False) -> dict:
        x_device_sim        = ''
        x_eezz_connection   = TSecureSocket()
        x_response          = x_eezz_connection.send_request('reqkey', [self.session.sid, self.manifest.get_key()], int(buy_request))
        x_json_response     = json.loads(x_response.decode('utf-8'))

        x_crpyt_key         = x_json_response.get('key')
        x_transaction_key   = x_json_response.get('transaction')

        if x_crpyt_key:
            x_document_key = self.decrypt_key_with_device(x_crpyt_key)
            x_json_response['return'] = {'code': 200, 'value': x_document_key}
        elif x_transaction_key:
            x_json_response['return'] = {'code': 100, 'value': x_transaction_key}
        return x_json_response

    def add_document_to_device(self) -> dict:
        x_response = self.session.send_bt_request(command='ADDDOC', args=[self.session.sid, self.manifest.get_key()])
        return x_response

    def get_device_key(self) -> bytes | None:
        x_response          = self.session.send_bt_request(command='GETKEY', args=[])
        if x_response['return']['code'] == 200:
            x_dev_keyvector64   = x_response['return']['value']
            return  base64.b64decode(x_dev_keyvector64)
        else:
            return None

    def decrypt_key_with_device(self, encrypted_key: bytes) -> bytes | None:
        """ Decrypt the document key

        :param encrypted_key: The encrypted key
        :return:              The decrypted key, vector pair
        """
        # Get the device key for decryption

        x_dev_keyvector     = self.get_device_key()
        if not x_dev_keyvector:
            return None

        x_encrypted_key_64  = encrypted_key
        x_encrypted_key     = base64.b64decode(x_encrypted_key_64)

        x_cipher            = AES.new(x_dev_keyvector[16:], AES.MODE_CBC, x_dev_keyvector[:16])
        x_doc_keyvector     = x_cipher.decrypt(x_encrypted_key)
        return x_doc_keyvector

    def encrypt_key_with_device(self, key: bytes, vector: bytes) -> bytes | None:
        """ Encrypt the document key

        :param key:     Document key
        :param vector:  Document vector
        :return:        encrypted document key
        """
        # Get the device key for encryption
        x_dev_keyvector     = self.get_device_key()
        if not x_dev_keyvector:
            return None

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

