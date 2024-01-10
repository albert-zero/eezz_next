import time
import tarfile
import json
import uuid
import base64
import logging

from io          import BufferedReader, BytesIO
from queue       import Queue
from filesrv     import TEezzFile, TFile
from service     import singleton, TService
from database    import TDatabaseTable
from blueserv    import TBluetooth
from pathlib     import Path

from threading   import Thread
from dataclasses import dataclass
from typing      import List
from Crypto      import Random
from Crypto.Cipher    import AES
from Crypto.Signature import PKCS1_v1_5
from Crypto.Hash      import SHA1, SHA256


@dataclass(kw_only=True)
class TManifest:
    """ The manifest represents the information for the EEZZ document """
    author:         str   = None
    price:          float = None
    currency:       str   = None
    files:          dict  = None
    descr_document: dict  = None
    named_values:   str   = None
    column_names:   List[str] = None

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

        self.keys_sections       = ['header', 'signature', 'files']
        self.column_names        = [x[0] for x in x_map_names]
        self.keys_section_header = [x[1] for x in x_map_names] + ['price', 'currency', 'files']
        self.keys_section_files  = ['name', 'hash', 'hash_list', 'size', 'chunk_size', 'type']

        self.descr_header   = {x: '' if x != 'files' else {} for x in self.keys_section_header}
        self.descr_files    = {x: '' for x in self.keys_section_files}
        self.descr_document = {'header': self.descr_header, 'signature': ''}

    def set_values(self, section: str, proposed: dict | bytes):
        try:
            if section == 'header':
                self.descr_document['header'].update({x: y for x, y in proposed.items() if x in self.keys_section_header})
            elif section == 'signature':
                self.descr_document['signature'] = base64.b64encode(proposed).decode('utf-8')
            elif section == 'files':
                x_file_descr = {x: y for x, y in proposed.items() if x in self.keys_section_files and x != 'name'}
                self.descr_document['header']['files'].update({proposed['name']: x_file_descr})
        except (TypeError, KeyError):
            pass

    def __str__(self):
        return json.dumps(self.descr_document, indent=4)


@singleton
@dataclass(kw_only=True)
class TMobileDevices(TDatabaseTable):
    """ Manage mobile device data for auto-login and document-key management """
    column_names: List[str] = None

    def __post_init__(self):
        self.title              = 'TUser'
        self.column_names       = ['CAddr', 'CDevice', 'CSid', 'CUser', 'CVector', 'CKey']
        super().__post_init__()

        for x in self.m_column_descr:
            x.type  = 'text'
            x.alias = x.header[1:].lower()

        self.m_column_descr[0].options     = 'not null'
        self.m_column_descr[0].primary_key = True
        super().prepare_statements()
        super().db_create()

    def get_couple(self, address: str):
        if not self.is_synchron:
            super().get_visible_rows(get_all=True)
        return super().do_select(row_id=address)


@singleton
@dataclass(kw_only=True)
class TDocuments(TDatabaseTable):
    """ Manages documents """
    column_names: List[str]       = None
    files_list:   List[TEezzFile] = None
    key:          bytes           = None
    vector:       bytes           = None
    description:  dict            = None
    manifest:     TManifest       = None
    eezz_path:    Path            = None
    name:         str             = None

    def __post_init__(self):
        self.manifest = TManifest()

        self.title            = 'TDocuments'
        self.column_names     = self.manifest.column_names
        super().__post_init__()

        for i, x in enumerate(self.m_column_descr):
            x.type  = 'text'
            x.alias = self.manifest.keys_section_header[i]

        for x in range(2):
            self.m_column_descr[x].options     = 'not null'
            self.m_column_descr[x].primary_key = True

        super().prepare_statements()
        super().db_create()

    def prepare_download(self, request: dict):
        """ Prepares the download of several files to include to an EEZZ document.
        The preparation puts all file descriptors into a queue and waits to all documents until the last download.
        This triggers the creation of the EEZZ document
        :param request: The json format of a WEB socket request
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

        self.manifest.set_values(section='header', proposed={'document_id':  uuid.uuid4()})
        self.manifest.set_values(section='header', proposed={'document_key': TBluetooth().encrypt_key(self.key, self.vector)})
        self.manifest.set_values(section='header', proposed=request)

        for x in x_files_descr:
            x_destination = x_path / f'{x["name"]}.crypt' if x['type'] == 'crypt' else x_path / x['name']
            self.files_list.append(
                TEezzFile(key=self.key, vector=self.vector, response=x_queue,
                          file_type=x['type'], chunk_size=x['chunk_size'], destination=x_destination, size=x['size']))
        Thread(target=self.create_document, daemon=True, args=(x_name, len(x_files_descr), x_queue)).start()

    def create_document(self, name: str, nr_files: int, queue: Queue[TEezzFile]) -> None:
        """ After all files downloaded, The document header is registered on eezz server and signed
        All files are zipped together with this header.
        :param name: Name of the document on disk
        :param nr_files: Number of files in the queue
        :param queue: The process queue
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

        x_document_id = self.manifest.descr_document['header']['document_id']
        x_header      = self.manifest.descr_document['header']
        x_response    = TBluetooth().register_document(x_document_id, x_header)

        if x_response['result']['code'] != 200:
            return

        x_signed_header = json.loads(x_response['result']['value'])
        x_document_path = TService().document_path / f'{name}.eezz'
        self.zip(manifest=x_signed_header, files=x_files)
        self.db_insert(self.manifest.descr_header)

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
            return {'return': {'code':530, 'value':'Signature verification failed'}}

        x_response  = json.loads(x_header.decode('utf-8'))
        x_response['return']  = {'code':200, 'value':'OK'}
        return x_response

    def buy(self):
        # get selected document
        x_key = self.selected_row['CKey']
        # buy and store the key in database
        x_response = TBluetooth().buy_document(x_key)
        # store result on database

    def zip(self, manifest: dict, files: List[TFile]):
        x_zip_stream = BytesIO()
        x_zip_stream.write(json.dumps(manifest).encode('utf-8'))
        x_zip_root   = Path(self.file_path.stem)

        x_file_list = list()
        for x_file in files:
            x_file_list.append({'name': x_file.destination.stem,
                                'type': x_file.file_type,
                                'path': x_zip_root, 'size': x_file.size, 'chunk_size': x_file.chunk_size,
                                'hash': x_file.hash_chain})

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
        :param manifest_only:
        :param document_key: If set, try to decrypt the document on the fly
        :return:The manifest of the document, which could be used for further processing of the data
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
                    except json.decoder.JSONDecodeError as x_except:
                        logging.error(msg="Manifest not in JSON format", stack_info=True, stacklevel=3)
                    continue
                if manifest_only:
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


# -- section for module tests
def test_manifest():
    logging.debug(msg='Test class TManifest')
    x_manifest = TManifest()
    x_manifest.set_values(section='header', proposed={'author': 'Albert', 'price': 14.5, 'currency': 'EUR'})
    x_manifest.set_values(section='files',   proposed={'size': 1000, 'chunk_size': 1024, 'name': 'test.doc'})
    x_manifest.set_values(section='files',   proposed={'size': 2000, 'chunk_size': 1024, 'name': 'preview.doc'})
    logging.debug(msg=str(x_manifest))


if __name__ == '__main__':
    x_service  = TService(root_path=Path(r'C:\Users\alzer\Projects\github\eezz_full\webroot'))
    x_log_path = x_service.logging_path / 'app.log'
    logging.basicConfig(filename=x_log_path, filemode='w', style='{', format='{name} - {levelname} - {message}')
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)

    my_doc = TDocuments()
    my_dev = TMobileDevices()

