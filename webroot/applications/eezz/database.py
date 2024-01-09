# -*- coding: utf-8 -*-
"""
    Copyright (C) 2015 www.EEZZ.biz (haftungsbeschränkt)

    TDatabase
    Create database from scratch.
    Encapsulate database access.
    Manage the RSA public key for communication with eezz server
    
"""
import sqlite3
import base64
import uuid
import json
import itertools

from   Crypto.Signature import PKCS1_v1_5
from   Crypto.Hash      import MD5, SHA1, SHA256
from   Crypto.Cipher    import AES
from   Crypto           import Random

from   service          import TService, singleton
from   dataclasses      import dataclass
from   table            import TTable, TNavigation, TTableRow, TTableColumn
from   typing           import Tuple, Any, List, Callable
from   pathlib          import Path


@dataclass(kw_only=True)
class TDatabaseColumn(TTableColumn):
    primary_key: bool = False
    options:     str  = ''
    alias:       str  = ''


@dataclass(kw_only=True)
class TDatabaseTable(TTable):
    """ General database management """
    statement_select: str       = None
    statement_count:  str       = None
    statement_create: str       = None
    statement_insert: str       = None
    database_path:    Path      = TService().database_path
    virtual_len:      int       = 0
    is_synchron:      bool      = False
    m_column_descr:   List[TDatabaseColumn] = None

    def __post_init__(self):
        super().__post_init__()
        self.select_option  = 'limit {limit} offset {offset}'
        self.select_sort    = 'order by {column_name} {order}'
        self.m_column_descr = [TDatabaseColumn(primary_key=False, alias=x.header, index=x.index, header=x.header, filter=x.filter, width=x.width, sort=x.sort) for x in self.m_column_descr]

        if not self.database_path.parent.exists():
            self.database_path.parent.mkdir(exist_ok=True)
        self.format_types['text'] = lambda x_size, x_val: ' {{:<{}}} '.format(x_size).format(x_val)

    def prepare_statements(self):
        x_sections = list()
        x_sections.append(f'create table if not exists {self.title}')
        x_sections.append(', '.join([f'{x.header} {x.type} {x.options}' for x in self.m_column_descr]))
        x_sections.append(', '.join([f'{x.header}' for x in itertools.filterfalse(lambda y: not y.primary_key, self.m_column_descr)]))
        self.statement_create = ' '.join([x_sections[0], x_sections[1], 'primary keys (', x_sections[2], ')'])

        self.statement_select = f""" select * from  {self.title} """
        self.statement_count  = f""" select count(*) as Count from {self.title} """

        x_sections.clear()
        x_sections.append(f'insert or replace into {self.title}')
        x_sections.append(', '.join([f'{x.header}' for x in self.m_column_descr]))
        x_sections.append(', '.join([f':{x.alias}' for x in self.m_column_descr]))
        self.statement_insert = ' '.join([x_sections[0], '(', x_sections[1], ')',  'values (', x_sections[2], ')'])

    def __len__(self):
        return self.virtual_len

    def db_select(self, statement: str, args: dict) -> List[TTableRow]:
        x_connection = sqlite3.connect(self.database_path)
        with x_connection:
            x_cursor  = x_connection.cursor()
            x_options = self.select_option.format(**{'limit': self.visible_items, 'offset': self.m_current_pos})

            x_cursor.execute(' '.join([statement, x_options]), (args,))
            x_list   = x_cursor.fetchall()
            self.data.clear()
            for x_row in x_list:
                self.append([x for x in x_row])
        x_connection.close()
        self.is_synchron = True
        return self.get_visible_rows()

    def db_create(self) -> None:
        """ Create the table on the database """
        x_connection = sqlite3.connect(self.database_path)
        with x_connection:
            x_cursor = x_connection.cursor()
            x_cursor.execute(self.statement_create)
        x_connection.close()

    def db_insert(self, row_data: dict) -> None:
        x_connection = sqlite3.connect(self.database_path)
        with x_connection:
            x_cursor = x_connection.cursor()
            x_cursor.execute(self.statement_insert.format(*self.column_names), (row_data,))
            self.append([x for x in row_data.values()])
        x_connection.close()

    def append(self, table_row: list, attrs: dict = None, row_type: str = 'body', row_id: str = '') -> None:
        x_row_descr = list(zip(table_row, self.m_column_descr))
        x_primary   = itertools.filterfalse(lambda x: not x[1].primary_key, x_row_descr)
        x_row_id    = SHA256.new(''.join(x[0] for x in x_primary).encode('utf8')).hexdigest()
        super().append(table_row=table_row, attrs=attrs, row_type=row_type, row_id=x_row_id)

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
                x_row: TTableRow = filter_row(TTableRow(['File']))
                x_filter_column  = x_row.column_descr[0]
                x_filter_value   = x_row[x_filter_column]

                x_where   = f'where {x_filter_column} like ?'
                x_cursor.execute(' '.join([self.statement_select, x_options, x_where]), (x_filter_value,))
            except (ValueError, IndexError):
                x_cursor.execute(' '.join([self.statement_select, x_options]))
            x_result = x_cursor.fetchall()
            for x_row in x_result:
                self.append([x for x in x_row], row_id=SHA256.new(x_row[0].encode('utf8')).hexdigest())
        self.is_synchron = True
        x_connection.close()
        return super().get_visible_rows(get_all=get_all)


# --- section for module tests
if __name__ == '__main__':
    pass
