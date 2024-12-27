
"""
This module implements the following classes

    * :py:class:`eezz.document.TManifest`: The Manifest contains the attributes and the content of a document.
    This includes for example author, creation date and embedded external files.
    * :py:class:`eezz.document.TDocument`:  A document consists of one or more embedded files and the Manifest.
    The class implements methods for file download and creating a TAR archive.
    A document has always a reference to a shelf, which contains documents with the same Manifest layout

"""
import os.path
import re
import time
import tarfile
import json
from   loguru         import logger

from io               import BytesIO
from filesrv          import TFile, TFileMode
from service          import TService
from pathlib          import Path
from dataclasses      import dataclass
from math             import floor
from typing           import List, Dict, override


@dataclass(kw_only=True)
class TManifest:
    """ The manifest represents the information for the document. It defines a solid way to ensures a consistent
    structure for parsing the internal attributes.
    """
    keys_section_header: list
    title:               str  = None
    keys_section_doc:    list = None
    keys_section_files:  list = None
    structure_document:  dict = None
    map_files:           dict = None

    def __post_init__(self):
        # Prepare consistent access of manifest to database
        self.keys_section_doc    = ['document', 'files', 'signature']
        self.keys_section_files  = ['source', 'name', 'size', 'type']
        self.structure_document  = {'document': {}, 'files': []}

    @property
    def document(self) -> dict:
        return self.structure_document['document']

    @document.setter
    def document(self, value: dict):
        self.map_files = dict()
        x_document_descr = {x: value.get(x, '') for x in self.keys_section_header}
        self.structure_document['document'] = x_document_descr

    @property
    def files(self) -> list:
        return self.structure_document['files']

    def append_file(self, file: dict):
        if not self.map_files.get(file['source']):
            self.map_files[file['source']] = list()

        x_file_descr = {x: file.get(x, '') for x in self.keys_section_files}
        self.structure_document['files'].append(x_file_descr)
        self.map_files[file['source']].append(file['name'])

    @property
    def column_names(self) -> list:
        return self.keys_section_header

    @override
    def __str__(self):
        self.structure_document['document']['title'] = self.title
        for x, y in self.map_files.items():
            self.structure_document['document'][x] = y
        return json.dumps(self.structure_document, indent=4)

    def loads(self, manifest_str):
        self.structure_document = json.loads(manifest_str)
        self.title = self.structure_document['document']['title']


@dataclass(kw_only=True)
class TDocument:
    """ Manages documents
    A document is a zipped TAR file, with a Metafile and a collection of data files.

    :ivar Path          path:            Documents bookshelf path
    :ivar List[str]     attributes:      List of attributes like author and title
    :ivar str           shelf_name:      A document has always a reference to a bookshelf
    :ivar TManifest     manifest:        Document header definition
    :ivar List[TFile]   files_list:      List of embedded files
    """
    shelf_name:   str
    attributes:   List[str]
    manifest:     TManifest         = None  #: :meta private:
    path:         Path              = None  #: :meta private:
    count:        int               = 0
    finished:     bool              = False
    file_sources: List[str]         = None

    def __post_init__(self):
        """ combine attributes:
        The mandatory attribute "title" is inserted at the start,
        the file sources at the end

        The file sources might have one or more references, which are represented as list of files in the Manif<est
        """
        self.map_files:         Dict[str, TFile] = dict()
        self.map_source:        Dict[str, list]  = dict()
        self.attributes = ['title'] + [x for x in self.attributes if x != 'title']

        if self.file_sources:
            self.attributes += self.file_sources

        if not self.path:
            self.path = TService().document_path
        self.manifest = TManifest(keys_section_header=self.attributes)

    def initialize_document(self, values: list):
        """ Initializes the document, providing values for the Manifest header

        :param values:  List of values according to the definition of columns
        :type  values:  List[str]
        """
        self.finished           = False
        self.count              = 0
        self.manifest.document  = {x: y for x, y in zip(self.attributes, values)}
        self.manifest.title     = values[0]

    def download_file(self, file: dict, stream: bytes = b'') -> bytes:
        """ Download file callback.
        The method is called for each chunk and for a final acknowledge. If all files
        are transferred, the document is created.
        For each final acknowledge a 100% is returned.

        :param file:    File descriptor with details on file and byte stream
        :type  file:    dict
        :param stream   File stream, usually a chunk of
        :param stream   bytearray
        :return;        The percentage of transferred document size as bytearray, terminated with the percent sign
        :rtype:         bytearray
        """
        if file['opcode'] == 'finished':
            # Check if we got all elements of a single source:
            if len(self.map_source[file['source']]) == file['volume']:
                self.count += 1

            # Check if we got all elements of all sources
            if self.count == len(self.file_sources):
                self.create_document()
                self.finished = True
            return b'100%'

        if not self.map_source.get(file['source']):
            self.map_source[file['source']] = list()

        if not self.map_files.get(file['name']):
            x_title = self.manifest.title
            x_path  = TService().document_path / x_title
            x_path.mkdir(exist_ok=True)
            x_path /= file['name']

            xt_file = TFile(file_type=file['source'], size = file['size'], chunk_size = file['chunk_size'], destination = x_path)
            self.map_files[file['name']] = xt_file
            self.map_source[file['source']].append(xt_file)
            self.manifest.append_file(file)

        xt_file = self.map_files[file['name']]
        xt_file.write(stream, file['sequence'], mode = TFileMode.NORMAL)
        x_percent = ((1 + int(file['sequence'])) * int(file['chunk_size'])) / xt_file.size
        x_percent = min(100, floor(x_percent))
        return f'{x_percent}%'.encode('utf8')

    def create_document(self):
        """ ZIP the given files and the manifest to a document.
        The TFile class keeps track on the location of the file content and their properties.
        """
        x_zip_title    = self.manifest.document['title']
        x_zip_stream   = BytesIO()
        x_zip_stream.write(str(self.manifest).encode('utf-8'))
        x_zip_root     = Path(x_zip_title)
        # Path is: destination / book / document
        x_destination  = self.path / f'{self.shelf_name}/{x_zip_title}.tar'
        x_destination.parent.mkdir(exist_ok=True)

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

    def read_file(self, document_title: str, file_name: str) -> bytes:
        """ Returns the bytestream of the specified file in the archive """
        x_source = self.path / f'{self.shelf_name}/{document_title}.tar'
        with tarfile.TarFile(x_source, "r") as x_zip_file:
            for x_tar_info in x_zip_file.getmembers():
                x_dest = Path(x_tar_info.name)
                if x_dest.name == file_name:
                    if x_buffer := x_zip_file.extractfile(x_tar_info):
                        return x_buffer.read()

    def extract_file(self, document_title: str, file_pattern: str = None, dest_root: Path = '.') -> None:
        """ Restores the specified files, given by the regular expression in file_pattern """
        if not file_pattern:
            file_pattern = r'\S*'

        x_source = self.path / f'{self.shelf_name}/{document_title}.tar'
        with tarfile.TarFile(x_source, "r") as x_zip_file:
            for x_tar_info in x_zip_file.getmembers():
                x_dest = Path(x_tar_info.name)
                if re.search(file_pattern, x_dest.name):
                    x_local_file = dest_root / x_dest
                    if x_buffer := x_zip_file.extractfile(x_tar_info):
                        with x_local_file.open('wb') as file:
                            file.write(x_buffer.read())


# -- section for module tests
def test_document():
    """:meta private:"""
    logger.debug('Test class Document')
    my_doc = TDocument(shelf_name='First', attributes=['title', 'desc', 'author', 'price', 'valid'], file_sources=['main'])

    x_file_source = TService().root_path / 'testdata/bird.jpg'
    x_size        = os.path.getsize(x_file_source)

    x_file_dest   = TService().document_path / 'title'
    x_file_dest.mkdir(exist_ok=True)
    x_file_dest   = x_file_dest/'bird.jpg'
    x_tfile       = TFile(file_type='main', destination=x_file_dest, size=x_size, chunk_size=x_size)
    with x_file_source.open('rb') as file:
        x_tfile.write(raw_data=file.read(), sequence_nr=0)

    my_doc.map_files['bird.jpg'] = x_tfile
    my_doc.initialize_document(['Level', 'descr', 'author', 'price', 'valid', 'bird.jpg'])
    my_doc.manifest.append_file({'name': 'bird.jpg', 'source': 'main', 'size': x_size, 'type': 'jpg'})
    my_doc.manifest.append_file({'name': 'bird.jpg', 'source': 'main', 'size': x_size, 'type': 'jpg'})
    my_doc.create_document()
    logger.debug(f'Created document {my_doc.path}/{my_doc.shelf_name}/{my_doc.manifest.document["title"]}.tar')


if __name__ == '__main__':
    """:meta private:"""
    TService.set_environment(root_path='/Users/alzer/Projects/github/eezz_full/webroot')
    test_document()
