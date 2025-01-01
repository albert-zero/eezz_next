"""
This module implements the following classes

    * :py:class:`example.bookshelf.TSimpleShelf`:
    * :py:class:`example.bookshelf.TDatabaseShelf`:

"""

from document       import TDocument
from dataclasses    import dataclass
from table          import TTable, TTableRow
from loguru         import logger
from database       import TDatabaseTable
from typing         import List, override
from service        import TService


@dataclass
class TSimpleShelf(TTable, TDocument):
    shelf_name:     str
    attributes:     List[str]   = None
    file_sources:   List[str]   = None
    column_names:   list        = None

    def __post_init__(self):
        """ Initialize the hierarchy of inheritance """
        # Adjust attributes
        self.attributes   = ['title', 'descr', 'price', 'valid']
        self.file_sources = ['main', 'detail']
        TDocument.__post_init__(self)

        # Set the column names and create the table
        self.column_names = self.attributes
        TTable.__post_init__(self)

        self.append(table_row=['' for x in self.column_names])

    def load_folder(self):
        # open folder self.title
        #   for each file glob('.tar') read manifest
        #       row = append document data: self.append(values) if instance str
        #       { source: list for source, list  in document data.items() }
        #       row[source].detail = list
        pass

    def prepare_document(self, values: list) -> TTableRow:
        """ Receive data from input form. This is the connection to the UI """
        self.initialize_document(values)
        return self.append(table_row = values)


@dataclass
class TDatabaseShelf(TDatabaseTable, TDocument):
    shelf_name:     str
    attributes:     List[str]   = None
    file_sources:   List[str]   = None
    column_names:   list        = None

    def __post_init__(self):
        """ Initialize the hierarchy of inheritance """
        self.attributes   = ['title', 'descr', 'price', 'valid']
        self.file_sources = ['main', 'detail']
        TDocument.__post_init__(self)

        # Set the column names and create the table
        # Hide the first line from database commit using TTable append
        self.database_name = f'{shelf_name}.db'
        self.column_names  = [f'C{x.capitalize()}' for x in self.attributes]

        TDatabaseTable.__post_init__(self)
        TTable.append(self, table_row=['' for x in self.column_names])

    def prepare_document(self, values: list) -> TTableRow:
        """ Receive data from input form. This is the connection to the UI """
        self.initialize_document(values)
        return self.append(table_row = values)

    def download_file(self, file: dict, stream: bytes = b'') -> bytes:
        x_result = super().download_file(file, stream)
        if self.finished:
            self.commit()
        return x_result


if __name__ == '__main__':
    TService.set_environment(root_path='/Users/alzer/Projects/github/eezz_full/webroot')
    tdoc = TSimpleShelf(shelf_name='First')
    x_manifest = tdoc.read_file('Level', 'Manifest')
    tdoc.print()
    logger.debug(f'{x_manifest}')
    logger.success('finished test TSimpleShelf')
