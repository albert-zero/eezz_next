# -*- coding: utf-8 -*-
"""
This module handles the database access and implements the following classes

    * :py:class:`eezz.database.TDatabaseTable`: Create database from scratch. Encapsulate database access.
    * :py:class:`eezz.database.TDatabaseColumn`: Extends the TTableColumn by parameters, which are relevant only for database access

The TDatabaseTable allows flexible usage of database and buffer access of data by switching seamlessly
and hence make standard access very easy and performant.
The database is created in sqlite3 in the first call of the class.

"""
import sqlite3
import logging
import itertools

from   Crypto.Hash      import SHA256

from   service          import TService
from   dataclasses      import dataclass
from   table            import TTable, TNavigation, TTableRow, TTableColumn
from   typing           import List
from   pathlib          import Path


@dataclass(kw_only=True)
class TDatabaseColumn(TTableColumn):
    """ Extension for column descriptor TTableColumn
    """
    primary_key: bool = False
    """ Property - Makes a column a primary key. In TTable the row-id is calculated as SHA256 hash on primary key values """
    options:     str  = ''
    """ Property - Database option for column creation (e.g. not null). |br| ``create table ... column text not null`` """
    alias:       str  = ''
    """  Property - Name for the column in prepared statements |br| ``insert <column>... values(:<alias>, ...)``  """


@dataclass(kw_only=True)
class TDatabaseTable(TTable):
    """ General database management
    Purpose of this class is a sophisticate work with database using an internal cache. All database
    operations are mapped to TTable. The column descriptor is used to generate the database table.
    The database results are restricted to the visible scope
    Any sort of data is launched to the database
    Only the first select statement is executed. For a new buffer, set member is_synchron to False
    The property column_names and title are inherited from TTable.

    :ivar statement_select: (str):   Select statement, inserting limit and offset according to TTable settings:|br|
        ``select <TTable.column_names> from <TTable.title>... limit <TTable.visible_items>... offset <TTable.offset>... where...``
    :ivar statement_count: (str):    Evaluates the number of elements in the database |br|
        ``select count (*) ...``
    :ivar statement_create: (str):    Create statement for database table:|br|
        ``create <TTable.title> <[List of TTable.column_names]> ... primary keys <[list of TDatabaseColumn.primary_key]>``
    :ivar is_synchron:  (bool): If True, select data from cache, else select from database
    :ivar column_descr: (List[TDatabaseColumn]): Properties for each column
    :ivar virtual_len:  (int): The number of entries in the database
    """
    statement_select: str       = None              #: :meta private: 
    statement_count:  str       = None              #: :meta private: 
    statement_create: str       = None              #: :meta private: 
    statement_insert: str       = None              #: :meta private: 
    database_path:    Path      = None              #: :meta private: 
    virtual_len:      int       = 0                 #: :meta private: 
    is_synchron:      bool      = False             #: :meta private: 
    column_descr:     List[TDatabaseColumn] = None  #: :meta private: 

    def __post_init__(self):
        """ Setup select options to restrict the data volume and sort directive.
         Add a new datatype  """
        super().__post_init__()
        self.database_path  = TService().database_path
        self.select_option  = 'limit {limit} offset {offset}'
        self.select_sort    = 'order by {column_name} {order}'
        self.column_descr   = [TDatabaseColumn(primary_key=False, alias=x.header, index=x.index, header=x.header, filter=x.filter, width=x.width, sort=x.sort) for x in self.column_descr]

        for x in self.column_descr:
            x.type = 'text'

        if not self.database_path.parent.exists():
            self.database_path.parent.mkdir(exist_ok=True)
        self.format_types['text'] = lambda x_size, x_val: ' {{:<{}}} '.format(x_size).format(x_val)
        self.prepare_statements()
        self.db_create()

    def prepare_statements(self):
        """ Generate a set of consistent database statements, used to select and navigate in database
        and to sync with TTable buffers
        """
        x_sections = list()
        x_sections.append(f'create table if not exists {self.title}')
        x_sections.append(', '.join([f'{x.header} {x.type} {x.options}' for x in self.column_descr]))
        x_sections.append(', '.join([f'{x.header}' for x in itertools.filterfalse(lambda y: not y.primary_key, self.column_descr)]))
        self.statement_create = ' '.join([x_sections[0], x_sections[1], f'primary keys ({x_sections[2]})'])

        self.statement_select = f""" select * from  {self.title} """
        self.statement_count  = f""" select count(*) as Count from {self.title} """

        x_sections.clear()
        x_sections.append(f'insert or replace into {self.title}')
        x_sections.append(', '.join([f'{x.header}' for x in self.column_descr]))
        x_sections.append(', '.join([f':{x.alias}' for x in self.column_descr]))
        self.statement_insert = ' '.join([x_sections[0], f'({x_sections[1]})',  f'values ({x_sections[2]})'])

    def __len__(self):
        """ Returns the result for a request: select count(*) ..."""
        return self.virtual_len

    def db_create(self) -> None:
        """ Create the table on the database using :py:attr:`eezz.table.TTable.column_names` and
        :py:attr:`eezz.table.TTable.title`
        """
        x_connection = sqlite3.connect(self.database_path)
        with x_connection:
            x_cursor = x_connection.cursor()
            x_cursor.execute(self.statement_create)
        x_connection.close()

    def append(self, table_row: list, attrs: dict = None, row_type: str = 'body', row_id: str = '', exists_ok: bool = True) -> TTableRow:
        """ Append data to the internal table, creating a unique row-key
        The row key is generated using the primary key values as comma separated list. You select from list as (no spaces)
        do_select(row_id = 'key_value1,key_value2,...')

        :param exists_ok:   If set to True, do not raise exception, just ignore the appending silently
        :param table_row:   A list of values as row to insert
        :type  table_row:   List[Any]
        :param attrs:       Optional attributes for this row
        :type  attrs:       dict
        :param row_type:    Row type used to trigger template output for HTML
        :param row_id:      Unique row-id, calculated internal, if not set
        :type  row_id:      SHA256 hash of primary key values
        :returns:           List of rows with length of TTable.visible_items
        :rtype:             List[TTableRow]
        """
        x_row_descr = list(zip(table_row, self.column_descr))
        if not row_id:
            x_primary   = itertools.filterfalse(lambda x: not x[1].primary_key, x_row_descr)
            row_id      = SHA256.new(','.join(x[0] for x in x_primary).encode('utf8')).hexdigest()

        # if the row already exists, we ignore the rest:
        if not attrs:
            attrs = dict()
        attrs['_database'] = 'new'
        super().append(table_row=table_row, attrs=attrs, row_type=row_type, row_id=row_id, exists_ok=False)

        x_connection = sqlite3.connect(self.database_path)
        with x_connection:
            x_cursor   = x_connection.cursor()
            x_row_data = {y.alias: x for x, y in x_row_descr}
            x_cursor.execute(self.statement_insert.format(*self.column_names), x_row_data)
            x_connection.close()

        x_row = super().append(table_row=table_row, attrs=attrs, row_type=row_type, exists_ok=True)
        return x_row

    def commit(self):
        """ Write all new entries to database, which have been added using method
        :py:meth:`~eezz.database.TDatabaseTable.append`
        """
        x: TTableRow
        x_connection = sqlite3.connect(self.database_path)
        with x_connection:
            x_cursor   = x_connection.cursor()

            for x in self.data:
                x_row_descr = list(zip(x.get_values_list(), self.column_descr))
                if x.attrs.get('_database') == 'new':
                    x.attrs.pop('_database')
                    x_row_data = {y.alias: x for x, y in x_row_descr}
                    x_cursor.execute(self.statement_insert.format(*self.column_names), x_row_data)
        x_connection.close()

    def navigate(self, where_togo: TNavigation = TNavigation.NEXT, position: int = 0) -> None:
        """ Navigate in block mode

        :param where_togo: Navigation direction
        :type  where_togo: TNavigation
        :param position:   Use database access if position > 0, disabling absolute positioning for database cursor\
        and make it easy to distinguish different access types.
        """
        super().navigate(where_togo=where_togo, position=position)
        if position == 0:
            self.is_synchron = False

    def get_visible_rows(self, get_all=False):
        """ Get visible rows. Works on local buffer for :py:attr:`eezz.database.TDatabaseTable.is_synchron`

        :param get_all: Ignore TTable.visible_items for this call
        """
        if self.is_synchron:
            return super().get_visible_rows(get_all=get_all)

        x_result_list = list()
        x_connection = sqlite3.connect(self.database_path)
        with x_connection:
            x_options = '' if get_all else self.select_option.format(**{'limit': self.visible_items, 'offset': self.offset})
            x_cursor  = x_connection.cursor()
            x_cursor.execute(' '.join([self.statement_select, x_options]))
            x_result = x_cursor.fetchall()

            for x_row in x_result:
                self.append([x for x in x_row])
                x_result_list.append([x for x in x_row])

        self.is_synchron = True
        x_connection.close()
        return x_result_list

    def do_select(self, filters: dict, get_all: bool = False) -> List[TTableRow]:
        """ Works on local buffer, as long as the scope is not changed. If send to database the syntax of the
        values have to be adjusted to
        `splite3-like <https://www.sqlitetutorial.net/sqlite-like/>`_

        :param get_all: Ignore TTable.visible_items for this call
        :param filters: Dictionary with column names as key and corresponding regular expression values to filter
        """
        x_result_list = list()

        if self.is_synchron:
            return super().do_select(filters=filters, get_all=get_all)

        x_connection = sqlite3.connect(self.database_path)
        with x_connection:
            x_options = '' if get_all else self.select_option.format(**{'limit': self.visible_items, 'offset': self.offset})
            x_cursor  = x_connection.cursor()

            x_where_stm = ' where '  + ' and '.join([f'{x} like ?' for x in filters.keys()])
            x_cursor.execute(' '.join([self.statement_select, x_options, x_where_stm]), [x for x in filters.values()])
            x_result = x_cursor.fetchall()

            for x_row in x_result:
                x_row = self.append([x for x in x_row])
                x_result_list.append(x_row)
            x_connection.close()

        self.is_synchron = True
        return x_result_list


# --- section for module tests
if __name__ == '__main__':
    """:meta private:"""
    x_service  = TService(root_path=Path(r'C:\Users\alzer\Projects\github\eezz_full\webroot'))
    x_log_path = x_service.logging_path / 'app.log'
    logging.basicConfig(filename=x_log_path, filemode='w', style='{', format='{name} - {levelname} - {message}')
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)

