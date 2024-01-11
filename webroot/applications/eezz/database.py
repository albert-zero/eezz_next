# -*- coding: utf-8 -*-
"""
This module implements the following classes
    * **TDatabaseTable**: Create database from scratch. Encapsulate database access.
    * **TDatabaseColumn**: Extends the TTableColumn by parameters, which are relevant only for database access

"""
import sqlite3
import logging
import itertools

from   Crypto.Hash      import SHA256

from   service          import TService
from   dataclasses      import dataclass
from   table            import TTable, TNavigation, TTableRow, TTableColumn
from   typing           import List, Callable
from   pathlib          import Path


@dataclass(kw_only=True)
class TDatabaseColumn(TTableColumn):
    """ Extension for column descriptor TTableColumn """
    primary_key: bool = False
    """ Makes a column a primary key """
    options:     str  = ''
    """ Options is used to crate a database column and could be for example: 'not null' """
    alias:       str  = ''      #
    """ 
    | Alias is the name for a column used to insert values:  
    | ``insert .... values (:alias, ...)``"""


@dataclass(kw_only=True)
class TDatabaseTable(TTable):
    """ General database management
    Purpose of this class is a sophisticate work with database using an internal cache. All database
    operations are mapped to TTable. The column descriptor is used to generate the database table.
    The database results are restricted to the visible scope
    Any sort of data is launched to the database
    Only the first select statement is executed. For a new buffer, set member is_synchron to False
    """
    statement_select: str       = None
    """ 
    | Select statement:
    | ``select <columns> form <table> ... limit ... offset ... where ...`` """
    statement_count:  str       = None
    """ 
    | Count elements and store result in 'virtual_len'
    | ``select count (*) ...`` """
    statement_create: str       = None
    """
    | Statement to create the table in the database
    | ``create <TTable.title> <[TTable.column_names]> ... primary keys <[DatabaseColumn.primary_key]>``  
    """
    statement_insert: str       = None
    database_path:    Path      = None
    virtual_len:      int       = 0
    is_synchron:      bool      = False
    m_column_descr:   List[TDatabaseColumn] = None

    def __post_init__(self):
        """ Setup select options to restrict the data volume and sort directive.
         Add a new datatype 'text to the output formatter '"""
        super().__post_init__()
        self.database_path  = TService().database_path
        self.select_option  = 'limit {limit} offset {offset}'
        self.select_sort    = 'order by {column_name} {order}'
        self.m_column_descr = [TDatabaseColumn(primary_key=False, alias=x.header, index=x.index, header=x.header, filter=x.filter, width=x.width, sort=x.sort) for x in self.m_column_descr]

        if not self.database_path.parent.exists():
            self.database_path.parent.mkdir(exist_ok=True)
        self.format_types['text'] = lambda x_size, x_val: ' {{:<{}}} '.format(x_size).format(x_val)

    def prepare_statements(self):
        """ Generate a set of consistent database statements for

        - create table

        - insert or replace

        - select requests

        """
        x_sections = list()
        x_sections.append(f'create table if not exists {self.title}')
        x_sections.append(', '.join([f'{x.header} {x.type} {x.options}' for x in self.m_column_descr]))
        x_sections.append(', '.join([f'{x.header}' for x in itertools.filterfalse(lambda y: not y.primary_key, self.m_column_descr)]))
        self.statement_create = ' '.join([x_sections[0], x_sections[1], f'primary keys ({x_sections[2]})'])

        self.statement_select = f""" select * from  {self.title} """
        self.statement_count  = f""" select count(*) as Count from {self.title} """

        x_sections.clear()
        x_sections.append(f'insert or replace into {self.title}')
        x_sections.append(', '.join([f'{x.header}' for x in self.m_column_descr]))
        x_sections.append(', '.join([f':{x.alias}' for x in self.m_column_descr]))
        self.statement_insert = ' '.join([x_sections[0], f'({x_sections[1]})',  f'values ({x_sections[2]})'])

    def __len__(self):
        """ Returns the result for a request: select count(*) ..."""
        return self.virtual_len

    def db_create(self) -> None:
        """ Create the table on the database """
        x_connection = sqlite3.connect(self.database_path)
        with x_connection:
            x_cursor = x_connection.cursor()
            x_cursor.execute(self.statement_create)
        x_connection.close()

    def db_insert(self, row_data: dict) -> None:
        """ Insert a table row to the database

        :param row_data: Values of the form {TDatabaseColumn.alias: value}
        :type row_data: dict
        """
        x_connection = sqlite3.connect(self.database_path)
        with x_connection:
            x_cursor = x_connection.cursor()
            x_cursor.execute(self.statement_insert.format(*self.column_names), row_data)
            self.append([row_data[x.alias] for x in self.m_column_descr])
        x_connection.close()

    def append(self, table_row: list, attrs: dict = None, row_type: str = 'body', row_id: str = '') -> None:
        """ Append data to the internal table, creating a unique row-key

        :param table_row: A row to insert
        :type table_row:  List of cell elements
        :param attrs:     Attributes for this row
        :type attrs:      Customizable dictionary
        :param row_type:  Row type to specify the output
        :param row_id:    If not set, the row-id is calculated from primary key values
        """
        if not row_id:
            x_row_descr = list(zip(table_row, self.m_column_descr))
            x_primary   = itertools.filterfalse(lambda x: not x[1].primary_key, x_row_descr)
            row_id      = SHA256.new(''.join(x[0] for x in x_primary).encode('utf8')).hexdigest()
        super().append(table_row=table_row, attrs=attrs, row_type=row_type, row_id=row_id)

    def do_select(self, row_id: str) -> TTableRow:
        return super().do_select(SHA256.new(row_id.encode('utf-8')).hexdigest())

    def navigate(self, where_togo: TNavigation = TNavigation.NEXT, position: int = 0) -> None:
        super().navigate(where_togo=where_togo, position=position)
        self.is_synchron = False

    def get_visible_rows(self, get_all=False, filter_row: Callable = lambda x: x):
        """ Works on local buffer, as long as the scope is not changed
        Entries found with filter_column are selected and inserted into the local buffer, if is_synchron set to false
        For the filter_column select the offset option for the result set is ignored """
        if self.is_synchron:
            return super().get_visible_rows(get_all=get_all)

        x_connection = sqlite3.connect(self.database_path)
        with x_connection:
            x_options  = '' if get_all else self.select_option.format(**{'limit': self.visible_items, 'offset': self.m_current_pos})
            x_cursor  = x_connection.cursor()

            try:
                # Customize as follows: filter_row=lambda x:
                # TTableRow([], attrs={'FilterColumn': 'value'})       -> select * .... where FilterColumn like value
                # TTableRow([Column1, Columns2], attrs={'FilterColumn': 'value'}) -> select Column1, Columns2  ....
                # The default filter would return None, which results into a default handling
                x_row: TTableRow | None = filter_row(None)
                for x, y in x_row.attrs.items():
                    x_cursor.execute(' '.join([self.statement_select, x_options, f'where {x} like ?']), (y,))
                    break
            except (ValueError, IndexError, AttributeError, TypeError):
                x_cursor.execute(' '.join([self.statement_select, x_options]))
            x_result = x_cursor.fetchall()
            for x_row in x_result:
                self.append([x for x in x_row])
        self.is_synchron = True
        x_connection.close()
        return super().get_visible_rows(get_all=get_all)


# --- section for module tests
if __name__ == '__main__':
    x_service  = TService(root_path=Path(r'C:\Users\alzer\Projects\github\eezz_full\webroot'))
    x_log_path = x_service.logging_path / 'app.log'
    logging.basicConfig(filename=x_log_path, filemode='w', style='{', format='{name} - {levelname} - {message}')
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)

