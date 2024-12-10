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
    """ Represents a database column as a subclass of a table column.

    This class provides additional features specific to database columns,
    including the ability to specify whether a column is a primary key, options
    related to its behavior or properties, and an alias for referencing the
    column under a different name. It is designed to be extended for specific
    use cases in managing and interacting with database tables.

    :ivar primary_key: Boolean flag indicating if the column is a primary key.
    :type primary_key: bool
    :ivar options: Additional options or settings for the column.
    :type options: str
    :ivar alias: Alternative name for referring to the column.
    :type alias: str
    """
    primary_key: bool = False  #: :meta private:
    options:     str  = ''     #: :meta private:
    alias:       str  = ''     #: :meta private:


@dataclass(kw_only=True)
class TDatabaseTable(TTable):
    """ Represents a database table, extending capabilities of a general table to include database interactions.

    The TDatabaseTable class is designed to manage a table within a database context. It handles the
    creation of database statements, synchronization with a database table, and data navigation and
    manipulation operations such as inserting records and committing changes. It uses the database
    path, name, and column descriptors to construct necessary SQL statements for operations.
    Initialization involves setting up the data structure and checking for primary keys.

    :ivar column_names: List of column names for the database table.
    :type column_names: list
    :ivar database_name: The name of the database file to connect or create.
    :type database_name: str
    :ivar statement_select: SQL select statement for the table.
    :type statement_select: str
    :ivar statement_count: SQL count statement for the table records.
    :type statement_count: str
    :ivar statement_create: SQL create statement to initialize the table.
    :type statement_create: str
    :ivar statement_insert: SQL insert statement for adding data to the table.
    :type statement_insert: str
    :ivar statement_where: List of conditions for SQL where clause.
    :type statement_where: list
    :ivar database_path: File path for the database.
    :type database_path: str
    :ivar virtual_len: Virtual length of the dataset.
    :type virtual_len: int
    :ivar column_descr: Description of each column in the database table.
    :type column_descr: List[TDatabaseColumn]
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
        """ Prepare SQL statements for a database table based on provided column descriptions.

        This method constructs SQL statements for creating, selecting, counting,
        and inserting data into a database table. The statements are built using
        the table title and column descriptions provided to the instance. The
        resulting SQL statements include a 'CREATE TABLE' statement with primary
        key constraint, a 'SELECT' statement to retrieve all data, a 'COUNT'
        statement to tally the rows, and an 'INSERT OR REPLACE' statement for
        adding or updating records.

        :param self: The instance of the class containing table title and
                     column descriptions required for preparing SQL statements.
        :return: None. The function operates by side-effect, updating instance
                 attributes with the generated SQL statements.
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
        """  Creates a new SQLite database using the specified database path. This method
        registers adapters and converters for the `datetime` type to facilitate proper
        storage and retrieval of datetime objects within the database.

        The method connects to the SQLite database using the provided path,
        executes the SQL statement designed to create the necessary database tables,
        and then closes the connection ensuring the changes are committed.

        :raises sqlite3.Error: If an error occurs while connecting to the database
            or executing the SQL statement.
        """
        sqlite3.register_adapter(datetime, lambda x_val: x_val.isoformat())
        sqlite3.register_converter("datetime", lambda x_val: datetime.fromisoformat(x_val.decode()))
        x_connection = sqlite3.connect(self.database_path)

        with x_connection:
            x_cursor = x_connection.cursor()
            x_cursor.execute(self.statement_create)

    def append(self, table_row: list, attrs: dict = None, row_type: str = 'body', row_id: str = '', exists_ok: bool = True) -> TTableRow:
        """ Appends a new row to the table with specified attributes and parameters.
        The function checks for a `row_id` and generates one if not provided,
        based on primary keys.

        Attributes are defaulted to include a '_database'
        key with value 'new'. This is used to manage values to be commited to the database with commit.

        The method accepts parameters to specify the type
        of the row, presence check, and any additional attributes.

        :param table_row: List of values representing the table row to append.
        :type table_row: list
        :param attrs: Optional dictionary of additional attributes. Defaults to None.
        :type attrs: dict, optional
        :param row_type: Type of the row, default is 'body'.
        :type row_type: str
        :param row_id: Identifier for the row. If not provided, it is generated automatically.
        :type row_id: str
        :param exists_ok: Indicates if appending should proceed without error if row exists. Defaults to True.
        :type exists_ok: bool
        :return: A reference to the appended table row.
        :rtype: TTableRow
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
        """ Commits new rows in the data to the database. This method iterates through
        entries marked as 'new' in their '_database' attribute, removes this marker,
        and then inserts the entries into the database using the provided insert
        statement and column names.

        :raises sqlite3.Error: If there is an error executing the database operations.
        """
        x_row: TTableRow
        with sqlite3.connect(self.database_path) as x_connection:
            x_cursor  = x_connection.cursor()
            x_new_row = itertools.filterfalse(lambda xf_row: xf_row.attrs.get('_database', None) != 'new', self.data)
            for x_row in x_new_row:
                x_row.attrs.pop('_database', None)
                x_cursor.execute(self.statement_insert.format(*self.column_names), tuple(x_row.get_values_list()))

    def navigate(self, where_togo: TNavigation = TNavigation.NEXT, position: int = 0) -> None:
        """ Navigate to a specified position within a data structure. This method
        allows navigation through different points based on the parameters provided. If the `position`
        is set to 0, synchronization is disabled by setting `is_synchron` to False.
        This allows to select data and restrict the number of data records transferred to the application.
        It allows the user to navigate in the selected data set if needed

        :param where_togo: The direction or target to which the navigation should occur. Defaults to
                           `TNavigation.NEXT`.
        :type where_togo: TNavigation
        :param position: The index or position to navigate to. If the value is 0, internal synchronization
                         will be set to False. Defaults to 0.
        :type position: int
        :return: None
        """
        super().navigate(where_togo=where_togo, position=position)
        if position == 0:
            self.is_synchron = False

    @override
    def get_visible_rows(self, get_all=False) -> list:
        """ Retrieves a list of visible rows from the data source. By default, it
        synchronizes the data if not already synchronized, clears any existing data
        in the process, and appends new data based on the current row filter
        description. The method subsequently yields visible rows as determined by
        the superclass implementation.

        :param get_all: A boolean to determine if all rows should be retrieved.
        :return: A list of visible rows.
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
