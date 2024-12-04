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
import os
from   datetime         import datetime, timezone

from   Crypto.Hash       import SHA256
from   typing_extensions import override

from   service           import TService
from   dataclasses       import dataclass
from   table             import TTable, TNavigation, TTableRow, TTableColumn
from   typing            import List
from   pathlib           import Path


@dataclass(kw_only=True)
class TDatabaseColumn(TTableColumn):
    """ Extension for column descriptor TTableColumn

    :param primary_key: Makes the column part of the primary key
    :type  primary_key: bool
    :param options:     Options for the database columns in "create table" statement like "not null"
    :type  options:     str
    """
    primary_key: bool = False  #: :meta private:
    options:     str  = ''     #: :meta private:
    alias:       str  = ''     #: :meta private:


@dataclass(kw_only=True)
class TDatabaseTable(TTable):
    """ General database management
    Purpose of this class is a sophisticate work with database using an internal cache. All database
    operations are mapped to TTable. The column descriptor of TTable is used to generate the database table.
    The number of rows selected from database will be restricted to the visible scope by default, thus
    omits performance issues for huge tables.

    :param column_names: List of column names
    :type  column_names: list[str]
    :param title:        Table name
    :type  title:        str

    The following variables could be used to overwrite special actions on automated database actions.
    You would need to overwrite the values after the creation of the class
    """
    column_names:     list      = None
    database_name:    str       = 'default.db'      #: :meta private:
    statement_select: str       = None              #: :meta private: 
    statement_count:  str       = None              #: :meta private: 
    statement_create: str       = None              #: :meta private: 
    statement_insert: str       = None              #: :meta private:
    statement_where:  list      = None              #: :meta private:
    database_path:    str       = None              #: :meta private:
    virtual_len:      int       = 0                 #: :meta private: 
    column_descr:     List[TDatabaseColumn] = None  #: :meta private:

    def __post_init__(self):
        """ Setup select options to restrict the data volume and sort directive.
         Add a new datatype  """
        x_full_path         = TService().document_path / self.database_name
        self.database_path  = x_full_path.as_posix()
        x_full_path.parent.mkdir(exist_ok=True)

        self.select_option  = 'limit {limit} offset {offset}'
        self.select_sort    = 'order by {column_name} {order}'

        if not self.column_names:
            # extract column names from descriptor and fill defaults
            self.column_names = list()
            for i, x_column in enumerate(self.column_descr):
                x_column.index = i
                x_column.alias = x_column.header if not x_column.alias else x_column.alias
                x_column.type  = 'text'          if not x_column.type  else x_column.type.lower()
                self.column_names.append(x_column.header)
        else:
            # Create column descriptor from column names
            self.column_descr = [TDatabaseColumn(header=x_name, alias=x_name, type='text', primary_key=(i == 0), index=i)
                                 for i, x_name in enumerate(self.column_names)]

        # No columns descriptions
        if len(self.column_descr) == 0:
            raise Exception('Table needs at least one column')

        # make sure, that there is a primary key
        if not (x_primary_keys := [x for x in itertools.filterfalse(lambda x: not x.primary_key, self.column_descr)]):
            raise Exception(f'Table needs at least one primary key')

        super().__post_init__()

        # define output formats
        self.format_types['text']    = self.format_types['str']
        self.format_types['integer'] = self.format_types['int']
        self.format_types['numeric'] = self.format_types['int']
        self.format_types['real']    = self.format_types['float']

        self.prepare_statements()
        self.create_database()

    def prepare_statements(self):
        """ :meta private:
        Generate a set of consistent database statements, used to select and navigate in database
        and to sync with TTable buffers
        """
        x_sections = list()
        x_sections.append(f'create table if not exists {self.title}')
        x_sections.append(', '.join([f'{x.header} {x.type} {x.options}' for x in self.column_descr]))
        x_sections.append(', '.join([f'{x.header}' for x in itertools.filterfalse(lambda y: not y.primary_key, self.column_descr)]))
        self.statement_create = f'{x_sections[0]}  ({x_sections[1]}, primary key ({x_sections[2]}))'

        self.statement_select = f""" select * from  {self.title} """
        self.statement_count  = f""" select count(*) as Count from {self.title} """

        x_sections.clear()
        x_sections.append(f'insert or replace into {self.title}')
        x_sections.append(', '.join(['?' for x in self.column_descr]))
        self.statement_insert = ' '.join([x_sections[0], f' values ({x_sections[1]})'])

    def __len__(self):
        """ :meta private: Returns the result for a request: select count(*) ..."""
        return self.virtual_len

    def create_database(self) -> None:
        """ Create the table on the database using
        :py:attr:`eezz.table.TTable.column_names` and
        :py:attr:`eezz.table.TTable.title`
        """
        sqlite3.register_adapter(datetime, lambda x_val: x_val.isoformat())
        sqlite3.register_converter("datetime", lambda x_val: datetime.fromisoformat(x_val.decode()))
        x_connection = sqlite3.connect(self.database_path)

        with x_connection:
            x_cursor = x_connection.cursor()
            x_cursor.execute(self.statement_create)

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
            row_id      = '/'.join(str(x[0]) for x in x_primary)

        if not attrs:
            attrs = dict()
        attrs['_database'] = 'new'
        x_row = super().append(table_row=table_row, attrs=attrs, row_type=row_type, row_id=row_id, exists_ok=exists_ok)
        return x_row

    def commit(self):
        """ Write all new entries to database, which have been added using method
        :py:meth:`~eezz.database.TDatabaseTable.append`
        """
        x_row: TTableRow
        with sqlite3.connect(self.database_path) as x_connection:
            x_cursor  = x_connection.cursor()
            x_new_row = itertools.filterfalse(lambda xf_row: xf_row.attrs.get('_database', None) != 'new', self.data)
            for x_row in x_new_row:
                x_row.attrs.pop('_database', None)
                x_cursor.execute(self.statement_insert.format(*self.column_names), tuple(x_row.get_values_list()))

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

    @override
    def get_visible_rows(self, get_all=False) -> list:
        """ Get visible rows. Works on local buffer for :py:attr:`eezz.database.TDatabaseTable.is_synchron`

        :param get_all: Ignore TTable.visible_items for this call
        """
        if not self.is_synchron:
            self.is_synchron = True
            self.data.clear()
            x_result = super().do_select(get_all=get_all, filter_descr=self.row_filter_descr)
            for x in x_result:
                self.append(list(x))
        yield from super().get_visible_rows(get_all=get_all)


def test_database():
    """:meta private:"""
    x_table  = TDatabaseTable(
            database_name = 'test.db',
            title         = 'Directory',
            column_descr  = [
                TDatabaseColumn(header='File',       type='text',   primary_key=True),
                TDatabaseColumn(header='Size',       type='integer'),
                TDatabaseColumn(header='AccessTime', type='text')])

    logging.debug(msg=f'database.py::main: insert elements and commit')
    x_path = Path.cwd()
    for x_item in x_path.iterdir():
        x_stat = os.stat(x_item.name)
        x_time = datetime.fromtimestamp(x_stat.st_atime, tz=timezone.utc)
        x_table.append([str(x_item.name), x_stat.st_size, x_time], attrs={'path': x_item}, row_id=x_item.name)
    x_table.commit()

    logging.debug(msg=f'database.py::main: select elements and print table')
    x_table.filter_rows([['Size > 20000'],['File like %py']])
    x_table.print()


# --- section for module tests
if __name__ == '__main__':
    """:meta private:"""
    x_service  = TService(root_path=Path(r'C:\Users\alzer\Projects\github\eezz_full\webroot'))

    x_log_path = x_service.logging_path / 'test_database.log'
    x_log_path.unlink(missing_ok=True)
    x_database = x_service.document_path / 'test.db'
    x_database.unlink(missing_ok=True)

    logging.basicConfig(filename=x_log_path, filemode='w', style='{', format='{name} - {levelname} - {message}')
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)

    logging.debug(msg=f'database.py::main: create database {x_database}')
    test_database()
