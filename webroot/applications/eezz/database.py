# -*- coding: utf-8 -*-
"""
    Copyright (C) 2015 www.EEZZ.biz (haftungsbeschränkt)

    TDatabase
    Create database from scratch.
    Encapsulate database access.
    Manage the RSA public key for communication with eezz server
    
"""
import sqlite3
from   Crypto.Hash  import MD5
from   service      import TService, singleton
from   dataclasses  import dataclass
from   table        import TTable, TNavigation, TTableRow
from   typing       import Tuple, Any, List
from   pathlib      import Path


@dataclass(kw_only=True)
class TDatabaseTable(TTable):
    """ General database management """
    statement_select: str = None
    statement_count:  str = None
    statement_create: str = None
    statement_insert: str = None
    database_path:    Path = TService().database_path
    virtual_len:      int  = 0
    is_synchron:      bool = False
    primary_key_inx:  int  = 0

    def __post_init__(self):
        super().__post_init__()
        self.select_option = 'limit {limit} offset {offset}'
        self.select_sort   = 'order by {column_name} {order}'
        if not self.database_path.parent.exists():
            self.database_path.parent.mkdir(exist_ok=True)

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
        x_row_key = ''.join([x.value for x in table_row[0:self.primary_key_inx]])
        x_row_id  = MD5.new(x_row_key.encode('utf8')).hexdigest()
        super().append(table_row=table_row, attrs=attrs, row_type=row_type, row_id=x_row_id)

    def navigate(self, where_togo: TNavigation = TNavigation.NEXT, position: int = 0) -> None:
        super().navigate(where_togo=where_togo, position=position)
        self.is_synchron = False

    def get_visible_rows(self, get_all=False, filter_column: Tuple[str, Any] = None):
        """ Works on local buffer, as long as the scope is not changed
        Entries found with filter_column are selected and inserted into the local buffer, if is_synchron set to false
        For the filter_column select the offset option for the result set is ignored """
        if self.is_synchron:
            return super().get_visible_rows(get_all=get_all, filter_column=filter_column)

        if filter_column is None:
            self.data.clear()

        x_connection = sqlite3.connect(self.database_path)
        with x_connection:
            x_filter  = ''
            x_values  = ('', )
            x_options  = '' if get_all else self.select_option.format(**{'limit': self.visible_items, 'offset': self.m_current_pos})
            x_cursor  = x_connection.cursor()
            if filter_column:
                x_filter  = f'where {filter_column[0]} = ?'
                x_options = '' if get_all else self.select_option.format(**{'limit': self.visible_items, 'offset': 0})
                x_values  = (filter_column[1],)
            x_cursor.execute(' '.join([self.statement_select, x_options, x_filter]), x_values)
            x_result = x_cursor.fetchall()
            for x_row in x_result:
                self.append([x for x in x_row])
        self.is_synchron = True
        x_connection.close()
        return super().get_visible_rows(get_all=get_all, filter_column=filter_column)


@singleton
@dataclass(kw_only=True)
class TMobileDevices(TDatabaseTable):
    column_names: List[str] = None

    def __post_init__(self):
        self.primary_key_inx = 0
        self.title            = 'TUser'
        self.column_names     = ['CAddr', 'CDevice', 'CSid', 'CUser', 'CVector', 'CKey']
        self.statement_create = """
            create table if not exists 
                {table} ({0} text PRIMARY KEY, {1} text, {2} text, {3} text, {4} text, {5} text) """.format(table=self.title, *self.column_names)
        self.statement_select = """ select * from  TUser """
        self.statement_count  = """ select count(*) as Count from {table} """.format(table=self.title)
        self.statement_insert = """ insert or replace into {table} ({0}, {1}, {2}, {3}, {4}, {5}) 
                                        values (:address, :device, sid, :user, :vector, :key)""".format(table=self.title, *self.column_names)
        super().__post_init__()
        super().db_create()

    def get_couple(self, address: str):
        if not self.is_synchron:
            super().get_visible_rows(get_all=True)
        return super().do_select(row_id=address)


@singleton
@dataclass(kw_only=True)
class TDocuments(TDatabaseTable):
    column_names: List[str] = None

    def __post_init__(self):
        self.primary_key_inx  = 1
        self.title            = 'TDocuments'
        self.column_names     = ['CID', 'CKey', 'CAddr', 'CTitle', 'CDescr', 'CLink', 'CStatus', 'CAuthor']
        self.statement_create = """ 
            create table if not exists 
                {table} ({0} text not null, {1} text not null, {2} text, {3} text, {4} text, {5} text, {6} text, {7} text, 
                    PRIMARY KEY ( {0}, {1} ))""".format(table=self.title, *self.column_names)
        self.statement_select = """ select * from {table}""".format(table=self.title)
        self.statement_insert = """ 
            insert or replace into {table} ({0}, {1}, {2}, {3} {4} {5} {6} {7}) 
                values (:doc_id, :doc_key, :address, :title, :descr, :link, :status, :author) """.format(table=self.title, *self.column_names)
        self.statement_count = """  select count(*) as Count from {table} """.format(table=self.title)
        super().__post_init__()
        super().db_create()


def test_mobile_devices():
    x_table = TMobileDevices()
    x_table.print()


def test_documents():
    x_table = TDocuments()
    x_table.print()


if __name__ == '__main__':
    test_mobile_devices()
    test_documents()

