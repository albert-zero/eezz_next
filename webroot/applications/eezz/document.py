
"""
This module implements the following classes

    * :py:class:`eezz.document.TManifest`: The Manifest contains the attributes and the content of a document.\
    This includes for example author, creation date and embedded external files.
    * :py:class:`eezz.document.TDocument`:  A document consists of one or more embedded files and the Manifest.\
    The class implements methods for file download and creating a TAR archive.

    A document has always a reference to a shelf, which contains documents with the same Manifest layout

"""
import  re
import  time
import  tarfile
import  json
from    loguru      import logger

from    abc         import abstractmethod
from    io          import BytesIO
from    filesrv     import TFile, TFileMode
from    service     import TService
from    pathlib     import Path
from    dataclasses import dataclass
from    math        import floor
from    typing      import List, Dict, override


@dataclass(kw_only=True)
class TManifest:
    """ The manifest represents the information for the document. It defines a solid way to ensures a consistent
    structure for parsing the internal attributes.
    """
    keys_section_header: list
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
        """:meta private:"""
        return self.structure_document['document']

    @document.setter
    def document(self, value: dict):
        """:meta private:"""
        self.map_files = dict()
        x_document_descr = {x: value.get(x, '') for x in self.keys_section_header}
        self.structure_document['document'] = x_document_descr

    @property
    def files(self) -> list:
        """:meta private:"""
        return self.structure_document['files']

    def append_file(self, file: dict):
        """:meta private:"""
        if not self.map_files.get(file['source']):
            self.map_files[file['source']] = list()

        x_file_descr = {x: file.get(x, '') for x in self.keys_section_files}
        self.structure_document['files'].append(x_file_descr)
        self.map_files[file['source']].append(file['name'])

    @property
    def column_names(self) -> list:
        """:meta private:"""
        return self.keys_section_header

    @override
    def __str__(self):
        for x, y in self.map_files.items():
            self.structure_document['document'][x] = y
        return json.dumps(self.structure_document, indent=4)

    def loads(self, manifest_str):
        """:meta private:"""
        self.structure_document = json.loads(manifest_str)


@dataclass(kw_only=True)
class TDocument:
    """ Manages documents
    A document is a zipped TAR file, w  ith a Metafile and a collection of data files.

    :ivar Path          path:            Documents bookshelf path
    :ivar List[str]     attributes:      List of attributes like author and title
    :ivar str           shelf_name:      A document has always a reference to a bookshelf
    :ivar TManifest     manifest:        Document header definition
    :ivar List[TFile]   files_list:      List of embedded files
    """
    shelf_name:     str                             #: :meta private:
    attributes:     List[str]                       #: List of document attributes like author and title
    manifest:       TManifest           = None      #: :meta private:
    path:           Path                = None      #: :meta private:
    count:          int                 = 0         #: :meta private:
    finished:       bool                = False     #: :meta private:
    file_sources:   List[str]           = None      #: :meta private:
    files_transferred: int              = 0
    map_files:      Dict[str, TFile]    = None      #: :meta private:
    map_source:     Dict[str, List[TFile]] = None   #: :meta private:
    transferred:    int                 = 0         #: :meta private:
    title:          str                 = ''        #: :meta private:

    def __post_init__(self):
        """ combine attributes:
        The mandatory attribute "title" is inserted at the start,
        the file sources at the end

        The file sources might have one or more references, which are represented as list of files in the Manif<est
        """
        self.attributes = [x for x in self.attributes]

        if self.file_sources:
            self.attributes += self.file_sources

        if not self.path:
            self.path = TService().document_path
        self.manifest = TManifest(keys_section_header=self.attributes)

    def initialize_document(self, values: list) -> None:
        """ Initializes the document, providing values for the Manifest header

        :param List[str] values:  List of values according to the definition of columns
        """
        self.finished           = False
        self.count              = 0
        self.manifest.document  = {x: y for x, y in zip(self.attributes, values)}
        self.map_source         = dict()
        self.map_files          = dict()
        self.transferred        = 0
        self.title              = values[0]

    def download_file(self, file: dict, stream: bytes = b'') -> bytes:
        """ Download file callback.
        The method is called for each chunk and for a final acknowledge. If all files
        are transferred, the document is created.
        For each final acknowledge a 100% is returned.

        :param dict      file:   File descriptor with details on file and byte stream
        :param bytearray stream: File stream, usually a chunk of
        :return: The percentage of transferred document size as bytearray, terminated with the percent sign
        :rtype:  bytearray

        """
        if file['opcode'] == 'finished':
            # Check if we got all elements of a single source:
            if self.transferred == file['all_volume']:
                self.create_document()
                self.finished = True
                return '100%'.encode('utf8')
            x_fraction = 100 * len(self.map_source[file['source']]) / int(file['src_files'])
            return f'{x_fraction}%'.encode('utf8')

        # Manage the file sources: Each source may have many entries
        # Drag hte number of successful loaded elements of this specific source
        if not self.map_source.get(file['source']):
            self.map_source[file['source']] = list()

        if not self.map_files.get(file['name']):
            x_path  = TService().public_path / self.title
            x_path.mkdir(exist_ok=True)
            x_path /= file['name']

            xt_file = TFile(file_type=file['source'], size = file['size'], chunk_size = file['chunk_size'], destination = x_path)
            self.map_files[file['name']] = xt_file
            self.map_source[file['source']].append(xt_file)
            self.manifest.append_file(file)

        xt_file = self.map_files[file['name']]
        xt_file.write(stream, file['sequence'], mode = TFileMode.NORMAL)

        # return percentage for a specific source
        self.transferred += xt_file.transferred

        x_src_volume = 0
        for xt_f in self.map_source[file['source']]:
            x_src_volume += xt_f.transferred

        x_percent  = 100 * x_src_volume / int(file['src_volume'])
        return f'{x_percent}%'.encode('utf8')

    @abstractmethod
    def create_document(self):
        """ Abstract method which is called after all files are in place """
        pass

    def create_archive(self, document_title: str) -> None:
        """ ZIP the given files and the manifest to a document.
        The TFile class keeps track on the location of the file content and their properties.

        :param str document_title: The name of the archive
        """
        x_zip_stream   = BytesIO()
        x_zip_stream.write(str(self.manifest).encode('utf-8'))
        x_zip_root     = Path('.')
        # Path is: destination / book / document
        x_destination  = self.path / f'{self.shelf_name}/{document_title}.tar'
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
        """ Returns the bytestream of the specified file in the archive

        :param str document_title:  The title of the document is the name of the archive
        :param str file_name:       The file content to return
        """
        x_source = self.path / f'{self.shelf_name}/{document_title}.tar'
        with tarfile.TarFile(x_source, "r") as x_zip_file:
            for x_tar_info in x_zip_file.getmembers():
                x_dest = Path(x_tar_info.name)
                if x_dest.name == file_name:
                    if x_buffer := x_zip_file.extractfile(x_tar_info):
                        return x_buffer.read()

    def extract_file(self, document_title: str, file_pattern: str = None, dest_root: Path = '.') -> None:
        """ Restores the specified files, given by the regular expression in file_pattern

        :param str  document_title: The document title is the name of the archive
        :param Path dest_root:      The path within the archive for all entries
        :param str  file_pattern:   The files to extract
        """
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
    logger.success('test document')


if __name__ == '__main__':
    """:meta private:"""
    TService.set_environment(root_path='/Users/alzer/Projects/github/eezz_full/webroot')
    test_document()
