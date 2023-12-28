# -*- coding: utf-8 -*-
"""
    Copyright (C) 2015 www.EEZZ.biz (haftungsbeschränkt)

    TDatabase
    Create database from scratch.
    Encapsulate database access.
    Manage the RSA public key for communication with eezz server
    
"""
import os
import base64
from   Crypto.PublicKey import RSA
import sqlite3
import json
from   service import TService, singleton
from   dataclasses import dataclass
from   table   import TTable, TNavigation
from   typing  import List
from   pathlib import Path


@dataclass
class TDatabaseTable(TTable):
    statement_select: str = None
    statement_count:  str = None
    statement_create: str = None
    statement_insert: str = None
    database_path:    Path = None
    virtual_len:      int  = 0
    is_synchron:      bool = False

    def __post_init__(self):
        super().__init__()
        self.select_option = 'limit {limit} offset {offset}'
        self.database_path = TService().database_path

    def __len__(self):
        return self.virtual_len

    def db_select(self, statement: str, args: dict) -> list:
        x_connection = sqlite3.connect(self.database_path)
        with x_connection:
            x_cursor = x_connection.cursor()
            x_cursor.execute(statement, (args,))
            x_list   = x_cursor.fetchall()
        x_connection.close()
        return x_list

    def db_create(self) -> None:
        """ Create the table on the database """
        if not self.statement_create:
            return
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

    def navigate(self, where_togo: TNavigation = TNavigation.NEXT, position: int = 0) -> None:
        super().navigate(where_togo=where_togo, position=position)
        self.is_synchron = False

    def get_visible_rows(self, get_all=False):
        if self.is_synchron:
            return super().get_visible_rows(get_all=get_all)

        super().data.clear()
        x_connection = sqlite3.connect(self.database_path)
        with x_connection:
            x_cursor = x_connection.cursor()
            x_detail = self.select_option.format(**{'limit': self.visible_items, 'offset': self.m_current_pos})
            x_cursor.execute(' '.join([self.statement_select, x_detail]).format(*self.column_names))
            x_result = x_cursor.fetchall()
            for x_row in x_result:
                self.append([x for x in x_row])
        self.is_synchron = True
        x_connection.close()
        return super().get_visible_rows(get_all=get_all)


@dataclass(kw_only=True)
class TDatabase:
    """ EEZZ database """
    def __post_init__(self):
        x_service     = TService()
        self.location = os.path.join(x_service.document_path, 'eezz.db')


@singleton
@dataclass(kw_only=True)
class TMobileDevices(TDatabaseTable):
    column_names: list = None

    def __post_init__(self):
        self.title = 'Users'
        self.column_names = ['CAddr', 'CDevice', 'CSid', 'CUser', 'CVector', 'CKey']
        self.statement_create = """
            create table if not exists 
                TUser ({0} text PRIMARY KEY, {1} text, {2} text, {3} text, {4} text, {5} text) """
        self.statement_select = """ select * from  TUser """
        self.statement_count  = """ select count(*) as Count from TUser """
        self.statement_insert = """ insert or replace into TUser ({0}, {1}, {2}, {3}, {4}, {5}) 
                                        values (:address, :device, sid, :user, :vector, :key)"""
        super().db_create()

    def get_address_list(self) -> list:
        return [x.cells[0].value for x in self.data]

    def get_couple(self, address: str):
        if not self.is_synchron:
            super().get_visible_rows(get_all=True)
        return super().do_select(row_id=address)


@singleton
class TDocuments(TDatabaseTable):
    column_names: list = None

    def __init__(self):
        self.title = 'User-Devices'
        self.column_names = ['CID', 'CKey', 'CAddr', 'CTitle', 'CDescr', 'CLink', 'CStatus', 'CAuthor']
        self.statement_create = """ 
            create table if not exists 
                TDocuments ({0} text not null, {1} text not null, {2} text, {3} text, {4} text, {5} text, {6} text, {7} text, 
                    PRIMARY KEY ({0}, {1}) )')"""
        self.statement_select = """ select * from TDocuments"""
        self.statement_insert = """ insert or replace into TDocuments ({0}, {1}, {2}, {3} {4} {5} {6} {/}) 
                                        values (:doc_id, :doc_key, :address, :title, :descr, :link, :status, :author) """
        self.statement_count = """  select count(*) as Count from TDocuments """
        super().db_create()


if __name__ == '__main__':
    pass
