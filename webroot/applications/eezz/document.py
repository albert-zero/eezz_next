"""
This module implements the following classes

    * :py:class:`eezz.document.TManifest`:   Document header representation. The header is a dictionary with a given \
    structure and a defined set of keys and sub-keys. The manifest defines the database table and access. \
    The manifest is the structure, which is signed and which is used to identify and verify the document.
    * :py:class:`eezz.document.TDocument`:  A document consists of more than one file and the manifest. Part of the \
    document is encrypted. The document key could be used in combination with a mobile device to decrypt the file.

The document module allows download of files in chunks and encryption/decryption.
The method :py:meth:`eezz.document.TDocument.zip` creates a partial encrypted archive with a signed header. the
manifest. The method unzip will check this header before unpacking.
There is a rudimentary idea implemented to trade self-consistent multi media files, using eezz server as transaction
platform.
"""
import time
import tarfile
import json
import math
from   loguru         import logger

from io               import BufferedReader, BytesIO
from filesrv          import TEezzFile, TFile, TFileMode
from service          import TService
from table            import TTable, TTableRow
from pathlib          import Path
from dataclasses      import dataclass
from typing import List, Dict, override
from math             import floor


@dataclass(kw_only=True)
class TManifest:
    """ The manifest represents the information for the EEZZ document. This structure is stored
    together with all other EEZZ files.
    """
    def __post_init__(self):
        # Prepare consistent access of manifest to database
        self.keys_section_doc    = ['document', 'files', 'signature']
        self.keys_section_header = ['title', 'descr', 'author', 'price', 'valid']
        self.keys_section_files  = ['source', 'name', 'size', 'type']

        self.structure_document  = {'document': {}, 'files': []}

    @property
    def document(self) -> dict:
        return self.structure_document['document']

    @document.setter
    def document(self, value: dict):
        x_document_descr = {x: value.get(x, '') for x in self.keys_section_header}
        self.structure_document['document'] = x_document_descr

    @property
    def files(self) -> list:
        return self.structure_document['files']

    def append_file(self, file: dict):
        x_file_descr = {x: file.get(x, '') for x in self.keys_section_files}
        self.structure_document['files'].append(x_file_descr)

    @property
    def column_names(self) -> list:
        return self.keys_section_header

    @override
    def __str__(self):
        return json.dumps(self.structure_document, indent=4)


@dataclass(kw_only=True)
class TDocuments(TTable):
    """ Manages documents

    :ivar title         str:             Document title
    :ivar Path          path:            Document root path
    :ivar List[TFile]   files_list:      List of files
    :ivar bytes         key:             Document key
    :ivar bytes         vector:          Document vector
    :ivar TManifest     manifest:        Document header
    """
    files_list:   List[TEezzFile]   = None  #: :meta private:
    manifest:     TManifest         = None  #: :meta private:
    path:         Path              = None  #: :meta private:
    column_names: list              = None

    def __post_init__(self):
        self.file_sources       = ['main', 'detail']
        self.manifest           = TManifest()
        self.column_names       = self.manifest.column_names + self.file_sources
        self.map_files:    Dict[str, TFile] = dict()
        self.map_source:   Dict[str, list]  = dict()
        self.count: int = 0
        self.document_title: str

        if not self.path:
            self.path = TService().document_path
        super().__post_init__()

        # The following line is used for the input form:
        x_row = self.append(['' for x in self.column_names], attrs={'files': self.file_sources})

    def prepare_document(self, values: list) -> TTableRow:
        """ Receive data from input form """
        x_row  = self.append(table_row = values, attrs={'files': self.file_sources})
        self.manifest.document  = {x: y for x, y in zip(self.column_names, values)}
        self.count = 0
        return x_row

    def download_file(self, file: dict, stream: bytes = b'') -> bytes:
        if file['opcode'] == 'finished':
            # check volume(source) with length of self.map_source[file['source']]
            # => count += 1
            # then: check count == number of source files: self.file_sources
            # => create document
            self.count += 1
            # Check if all sources are finished with upload:
            if self.count == 2:
                self.create_document()
            return b'100%'

        if not self.map_source.get(file['source']):
            self.map_source[file['source']] = list()

        if not self.map_files.get(file['name']):
            x_title = self.title
            x_path  = TService().document_path / x_title
            x_path.mkdir(exist_ok=True)
            x_path /= file['name']

            xt_file = TFile(file_type=file['source'], size = file['size'], chunk_size = file['chunk_size'], destination = x_path)
            self.map_files[file['name']] = xt_file
            self.map_source[file['source']].append(xt_file)
            self.manifest.append_file(file)

        xt_file = self.map_files[file['name']]
        xt_file.write(stream, file['sequence'], mode = TFileMode.NORMAL)
        x_percent = (int(file['sequence']) * int(file['chunk_size'])) / xt_file.size
        x_percent = min(100, math.floor(x_percent))
        return f'{x_percent}%'.encode('utf8')

    def create_document(self):
        """ Zip the given files and the manifest to an EEZZ document.
        """
        x_zip_stream   = BytesIO()
        x_zip_stream.write(str(self.manifest).encode('utf-8'))
        x_zip_root     = self.path / self.title
        x_destination  = x_zip_root / f'{self.title}.tar'

        with tarfile.TarFile(x_destination, "w") as x_zip_file:
            # store the info at the start of the tar file
            x_entry_path       = Path(x_zip_root) / 'Manifest'

            x_tar_info         = tarfile.TarInfo(name=str(x_entry_path))
            x_tar_info.size    = x_zip_stream.tell()
            x_tar_info.mtime   = floor(time.time())

            x_zip_stream.seek(0)
            x_zip_file.addfile(tarinfo=x_tar_info, fileobj=x_zip_stream)

            # Sort the files by source for output zip:
            for x_name, x_file in self.map_files.items():
                x_entry_path     = Path(x_zip_root) / x_file.destination.name
                x_source_path    = x_file.destination
                x_stat_info      = x_source_path.stat()
                x_tar_info       = tarfile.TarInfo(name=str(x_entry_path))
                x_tar_info.size  = x_stat_info.st_size
                x_tar_info.mtime = floor(time.time())

                with x_source_path.open("rb") as x_input:
                    x_zip_file.addfile(tarinfo=x_tar_info, fileobj=x_input)

    def unzip(self, source: Path, dest_root: Path = '.', manifest_only=False) -> dict:
        """ Unzip a file and return the Manifest in JSON format.

        :param source:          The source path of the file to unzip
        :param dest_root:       The destination root, where to extract the files to
        :param manifest_only:   If true, extract the manifest only
        :return: Returns the manifest
        """
        x_manifest = dict()
        with tarfile.TarFile(source, "r") as x_zip_file:
            for x_tar_info in x_zip_file.getmembers():
                x_dest = Path(x_tar_info.name)
                if not x_tar_info.name.endswith('Manifest'):
                    continue

                x_dest.mkdir(exist_ok=True)
                x_buffer: BufferedReader | None = x_zip_file.extractfile(x_tar_info)
                if x_buffer:
                    x_manifest = json.loads(x_buffer.read().decode('utf8'))
                if manifest_only:
                    return x_manifest

            for x_tar_info in x_zip_file.getmembers():
                x_dest = Path(x_tar_info.name)
                if x_tar_info.name.endswith('Manifest'):
                    continue

                x_buffer: BufferedReader | None = x_zip_file.extractfile(x_tar_info)
                with x_dest.open('wb') as file:
                    file.write(x_buffer.read())
        return x_manifest


# -- section for module tests
def test_manifest():
    """:meta private:"""
    logger.debug('Test class TManifest')
    my_doc = TDocuments(title='MyDoc')
    my_doc.prepare_document(['title', 'descr', 'albert', 100.0, '01.05.2026', 'file1', 'file2'])
    my_doc.manifest.append_file({'name': 'file1.name', 'source': 'file1', 'size': 10000, 'type': 'text'})
    my_doc.manifest.append_file({'name': 'file2.name', 'source': 'file2', 'size': 10000, 'type': 'text'})
    logger.debug(str(my_doc.manifest))


if __name__ == '__main__':
    """:meta private:"""
    TService.set_environment(root_path='/Users/alzer/Projects/github/eezz_full/webroot')
    test_manifest()