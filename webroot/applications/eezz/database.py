# -*- coding: utf-8 -*-
"""
    * **TDatabaseTable**:   Create database from scratch. Encapsulate database access.
    * **TDatabaseColumn**:  Extends the TTableColumn by parameters, which are relevant only for database access

"""
import sqlite3
import logging
import itertools

from   Crypto.Hash      import SHA256

from   service          import TService
from   dataclasses      import dataclass, InitVar
from   table            import TTable, TNavigation, TTableRow, TTableColumn, TTableInsertException
from   typing           import List, Callable
from   pathlib          import Path


@dataclass(kw_only=True)
class TDatabaseColumn(TTableColumn):
    """ Extension for column descriptor TTableColumn

    :param primary_key: Makes a column a primary key. In TTable the row-id is calculated as SHA256 hash on primary key values.
    :param options:     Database option for column creation (e.g. not null). |br| ``create table ... column text not null``
    :param alias:       Name for the column in prepared statements: |br| ``insert <column>... values(:<alias>, ...)``
    """
    primary_key: bool = False
    """ :meta private: """
    options:     str  = ''
    """ :meta private: """
    alias:       str  = ''
    """ :meta private: """


@dataclass(kw_only=True)
class TDatabaseTable(TTable):
    """ General database management
    Purpose of this class is a sophisticate work with database using an internal cache. All database
    operations are mapped to TTable. The column descriptor is used to generate the database table.
    The database results are restricted to the visible scope
    Any sort of data is launched to the database
    Only the first select statement is executed. For a new buffer, set member is_synchron to False

    :param statement_select:    Select statement, inserting limit and offset according to TTable settings:|br|
        ``select <TTable.column_names> from <TTable.title>... limit <TTable.visible_items>... offset <TTable.offset>... where...``
    :param statement_count:     Evaluates the number of elements in the database |br|
        ``select count (*) ...``
    :param statement_create:    Create statement for database table:|br|
        ``create <TTable.title> <[List of TTable.column_names]> ... primary keys <[list of TDatabaseColumn.primary_key]>``
    """
    column_names:     list      = None
    """ :meta private:  """
    statement_select: str       = None
    """ :meta private:  """
    statement_count:  str       = None
    """ :meta private:  """
    statement_create: str       = None
    """ :meta private:  """
    statement_insert: str       = None
    """ :meta private:  """
    database_path:    Path      = None
    """ :meta private:  """
    virtual_len:      int       = 0
    """ :meta private:  """
    is_synchron:      bool      = False
    """ :meta private:  """
    column_descr:   List[TDatabaseColumn] = None
    """ :meta private:  """

    def __post_init__(self):
        """ Setup select options to restrict the data volume and sort directive.
         Add a new datatype 'text to the output formatter '"""
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

    def prepare_statements(self):
        """ Generate a set of consistent database statements for
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
        """ Create the table on the database """
        x_connection = sqlite3.connect(self.database_path)
        with x_connection:
            x_cursor = x_connection.cursor()
            x_cursor.execute(self.statement_create)
        x_connection.close()

    def append(self, table_row: list, attrs: dict = None, row_type: str = 'body', row_id: str = '', exists_ok: bool = True) -> TTableRow:
        """ Append data to the internal table, creating a unique row-key
        The row key is generated using the primary key values as comma separated list. You select from list as (no spaces)
        do_select(row_id = 'key_value1,key_value2,...')

        :param exists_ok: If set to True, do not raise exception, just ignore the appending silently
        :param table_row: A list of values as row to insert
        :type table_row:  List of values in correct order to columns
        :param attrs:     Optional attributes for this row
        :type  attrs:     dict
        :type attrs:      Customizable dictionary
        :param row_type:  Row type used to trigger template output
        :param row_id:    If not set, the row-id is calculated from primary key values
        """
        x_row_descr = list(zip(table_row, self.column_descr))
        if not row_id:
            x_primary   = itertools.filterfalse(lambda x: not x[1].primary_key, x_row_descr)
            row_id      = SHA256.new(','.join(x[0] for x in x_primary).encode('utf8')).hexdigest()

        # if the row already exists, we ignore the rest:
        super().append(table_row=table_row, attrs=attrs, row_type=row_type, row_id=row_id, exists_ok=False)
        x_connection = sqlite3.connect(self.database_path)

        with x_connection:
            x_cursor   = x_connection.cursor()
            x_row_data = {y.alias: x for x, y in x_row_descr}
            x_cursor.execute(self.statement_insert.format(*self.column_names), x_row_data)
            x_connection.close()

        x_row = super().append(table_row=table_row, attrs=attrs, row_type=row_type, exists_ok=True)
        return x_row

    def navigate(self, where_togo: TNavigation = TNavigation.NEXT, position: int = 0) -> None:
        super().navigate(where_togo=where_togo, position=position)
        self.is_synchron = False

    def get_visible_rows(self, get_all=False):
        """ Works on local buffer, as long as the scope is not changed
        """
        x_result_list = list()

        if self.is_synchron:
            return super().get_visible_rows(get_all=get_all)

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

    def do_select(self, columns: List[str], values: List[str], get_all: bool = False) -> List[TTableRow]:
        """ Works on local buffer, as long as the scope is not changed
        """
        x_result_list = list()

        if self.is_synchron:
            return super().do_select(columns=columns, values=values, get_all=get_all)

        x_connection = sqlite3.connect(self.database_path)
        with x_connection:
            x_options = '' if get_all else self.select_option.format(**{'limit': self.visible_items, 'offset': self.offset})
            x_cursor  = x_connection.cursor()

            x_where_stm = ' where '  + ' and '.join([f'{x} like ?' for x, y in columns])
            x_cursor.execute(' '.join([self.statement_select, x_options, x_where_stm]), values)
            x_result = x_cursor.fetchall()

            for x_row in x_result:
                x_row = self.append([x for x in x_row])
                x_result_list.append(x_row)
            x_connection.close()

        self.is_synchron = True
        return x_result_list


# --- section for module tests
if __name__ == '__main__':
    x_service  = TService(root_path=Path(r'C:\Users\alzer\Projects\github\eezz_full\webroot'))
    x_log_path = x_service.logging_path / 'app.log'
    logging.basicConfig(filename=x_log_path, filemode='w', style='{', format='{name} - {levelname} - {message}')
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)

