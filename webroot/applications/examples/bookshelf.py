"""
This module implements the following classes

    * :py:class:`example.bookshelf.TSimpleShelf`:
    * :py:class:`example.bookshelf.TDatabaseShelf`:

"""
from numpy.ma.extras import column_stack

from document       import TDocument
from dataclasses    import dataclass
from table          import TTable, TTableRow, TTableCell, TTableCellDetail
from loguru         import logger
from database       import TDatabaseTable
from typing         import List, override
from service        import TService


class TFormInput(TTable):
    def __init__(self, title: str):
        super().__init__(column_names=['Title', 'Description', 'Medium', 'Technique', 'Price'], title=title)
        self.visible_items = 1
        self.append(['' for x in self.column_names])

    def register(self, input_row: list) -> None:
        print(f"Register...{input_row}")
        super().append(input_row)


@dataclass
class TSimpleShelf(TTable, TDocument):
    shelf_name:     str
    attributes:     List[str]   = None
    file_sources:   List[str]   = None
    column_names:   list        = None
    current_row:    TTableRow   = None
    id:             str         = ''

    def __post_init__(self):
        """ Initialize the hierarchy of inheritance """
        # Adjust attributes
        self.attributes   = ['Title', 'Header', 'Description', 'Medium', 'Technique', 'Price', 'Size', 'Status']
        self.file_sources = ['main', 'detail']
        self.id           = self.shelf_name
        TDocument.__post_init__(self)

        # Set the column names and create the table
        self.column_names = self.attributes
        TTable.__post_init__(self)

        self.visible_items = 1
        self.append(table_row=['' for x in self.column_names], row_type='input')

    def prepare_document(self, values: list) -> TTableRow:
        """ Receive data from input form and stores the values into the table """
        self.initialize_document(values)
        self.current_row = self.append(table_row = values, row_id=values[0])
        self.current_row['Status'] = ''
        return self.current_row

    @override
    def create_document(self):
        """ This method is called, after download of all files """
        if self.map_source.get('detail'):
            x_inx               = self.column_names.index('detail')
            x_cell: TTableCell  = self.current_row.cells[x_inx]
            x_cell.detail       = [x_ft.name for x_ft in self.map_source['detail']]
        super().create_archive(document_title=self.manifest.document['Title'])

    def load_documents(self) -> TTableRow:
        """ Load the bookshelf from scratch """
        self.clear()
        self.visible_items = 20

        x_path = self.path / self.shelf_name
        for x in x_path.glob('*.tar'):
            self.manifest.loads(self.read_file(x.stem, 'Manifest'))
            x_row_values = [x_val for x_key, x_val in self.manifest.document.items()]
            x_row        = self.append(x_row_values, row_id=x_row_values[0])
            x_row['Status'] = ''
        return self.selected_row


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
        self.database_name = f'{self.shelf_name}.db'
        self.column_names  = [f'C{x.capitalize()}' for x in self.attributes]

        TDatabaseTable.__post_init__(self)
        TTable.append(self, table_row=['' for x in self.column_names])

    @override
    def create_document(self):
        super().create_archive(document_title=self.manifest.document['Title'])

    @override
    def on_select(self, row: str) -> TTableRow | None:
        x_row = super().on_select(row)
        self.manifest.document = {x: y for x, y in zip(self.column_descr, x_row.get_values_list())}
        return x_row

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
