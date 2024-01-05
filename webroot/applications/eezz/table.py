#!/usr/bin/python3
"""
  Copyright (C) 2015  Albert Zedlitz

  TTable is used for formatted ASCII output of a table structure. 
  It allows to access the table data for further processing e.g. for HTML output.
  It could also be used to access a SQL database table

  TTable is a list of TTableRow objects, each of which is a list of TCell objects.
  The TColumn holds the column names and is used to organize sort and filter.
  A TCell object could hold TTable objects for recursive tree structures.
"""

import os
import collections
from   dataclasses import dataclass
from   typing      import List, Dict, NewType, Tuple, Any, Callable
from   enum        import Enum
from   pathlib     import Path
from   datetime    import datetime, timezone
from   threading   import Condition
from   copy        import deepcopy
import sqlite3


class TNavigation(Enum):
    """ Navigation control enum to navigate block steps in table """
    ABS  = 0
    NEXT = 1
    PREV = 2
    TOP  = 3
    LAST = 4


class TSort(Enum):
    """ Sorting control enum to define sort on columns """
    NONE       = 0
    ASCENDING  = 1
    DESCENDING = 2


@dataclass(kw_only=True)
class TTableCell:
    """ Table cell is the smallest unit of a table """
    width:  int
    value:  int | float | str | datetime
    index:  int  = 0
    type:   str  = 'str'
    attrs:  dict = None


@dataclass(kw_only=True)
class TTableColumn:
    """ Summarize the cell properties in a column
    which includes sorting and formatting """
    index:  int
    header: str
    width:  int   = 10
    filter: str   = ''
    sort:   TSort = TSort.NONE
    type:   str = ''
    attrs:  dict = None


# forward declaration
TTable = NewType('TTable', None)


@dataclass(kw_only=True)
class TTableRow:
    """ This structure is created for each row in a table
    It allows also to specify a sub-structure table """
    cells:        List[TTableCell]
    cells_filter: List[TTableCell] | None = None
    column_descr: List[str]   = None
    index:        int    = None
    row_id:       str    = None
    child:        TTable = None
    type:         str    = 'body'
    attrs:        dict   = None

    def __post_init__(self):
        if self.attrs:
            for x, y in self.attrs.items():
                setattr(self, x, y)

    def get_values_list(self) -> list:
        return [x.value for x in self.cells]

    def get_value(self, column: str):
        try:
            x_inx = self.column_descr.index(column)
            return self.cells[x_inx].value
        except ValueError:
            return None

    def __getitem__(self, column: int | str) -> Any:
        x_inx = column if type(column) is int else self.column_descr.index(column)
        return self.cells[x_inx].value


@dataclass(kw_only=True)
class TTable(collections.UserList):
    """ The table is derived from User-list to enable sort and list management """
    column_names:        List[str]
    column_names_map:    Dict[str, TTableCell] | None = None
    column_names_alias:  Dict[str, str]        | None = None
    column_names_filter: List[int]             | None = None

    virtual_table:  TTable      = None
    title:          str         = 'Table'
    attrs:          dict        = None
    visible_items:  int         = 20
    m_current_pos:  int         = 0
    m_column_descr: List[TTableColumn] = None
    selected_row:   TTableRow   = None
    header_row:     TTableRow   = None
    apply_filter:   bool        = False
    format_types:   dict        = None
    table_index:    Dict[str, TTableRow] = None

    def __post_init__(self):
        """ Post init for a data class """
        super().__init__()
        self.table_index      = dict()
        self.m_column_descr   = [TTableColumn(index=x_inx, header=x_str, filter=x_str, width=len(x_str), sort=TSort.NONE) for x_inx, x_str in enumerate(self.column_names)]
        x_cells               = [TTableCell(value=x_str, index=x_inx, width=len(x_str)) for x_inx, x_str in enumerate(self.column_names)]
        self.header_row       = TTableRow(cells=x_cells, type='header')
        self.column_names_map = {x.value: x for x in x_cells}

        if not self.format_types:
            self.format_types = {
                'int':      lambda x_size, x_val: ' {{:>{}}} '.format(x_size).format(x_val),
                'str':      lambda x_size, x_val: ' {{:<{}}} '.format(x_size).format(x_val),
                'float':    lambda x_size, x_val: ' {{:>{}.2}} '.format(x_size).format(x_val),
                'datetime': lambda x_size, x_val: ' {{:>{}}} '.format(x_size).format(x_val.strftime("%m/%d/%Y, %H:%M:%S"))}

    def filter_clear(self):
        self.apply_filter  = False

    def filter_columns(self, column_names: List[Tuple[str, str]]) -> None:
        """ First tuple value is the column name at any position, second tupel value is the new column display name
        The filter is used to generate customized output. This function could also be used to reduce the number of
        visible columns """
        # Create a list of column index and a translation of the column header entry
        self.column_names_filter = list()
        self.column_names_alias  = {x: y for x, y in column_names}
        for x, y in column_names:
            try:
                x_inx = self.column_names_map[x].index
                self.column_names_filter.append(x_inx)
                self.m_column_descr[x_inx].filter = y
                self.apply_filter = True
            except KeyError:
                pass

    def append(self, table_row: list, attrs: dict = None, row_type: str = 'body', row_id: str = '') -> None:
        """ Append a row into the table
        This procedure also defines the column type and the width """
        # define the type with the first line inserted
        x_inx       = len(self.data)
        x_row_descr = list(zip(table_row, self.m_column_descr))

        if row_id == '':
            row_id = str(x_inx)

        if x_inx == 0:
            self.table_index.clear()
            for x_cell, x_descr in x_row_descr:
                x_descr.type = type(x_cell).__name__

        # Check if the row-id is unique
        if self.table_index.get(row_id):
            return

        x_cells = [TTableCell(width=len(str(x_cell)), value=x_cell, index=x_descr.index, type=x_descr.type) for x_cell, x_descr in x_row_descr]
        x_row   = TTableRow(index=x_inx, cells=x_cells, attrs=attrs, type=row_type, row_id=row_id, column_descr=self.column_names)

        super(collections.UserList, self).append(x_row)
        self.table_index[row_id] = x_row

        for x_cell, x_descr in x_row_descr:
            x_descr.width = max(len(str(x_cell)), x_descr.width)
        if not self.selected_row:
            self.selected_row = x_row
        # todo : manage index self.table_index[row_id] = x_row and clear: self.table_index = dict()
        # if: self.table_index.get( row_id ) is None insert( row_id ) else throw KeyError:

    def get_header_row(self) -> TTableRow:
        """ Returns the header row. A filter for header values could be applied """
        if self.apply_filter:
            # Select the visible columns in the desired order and map the new names
            self.header_row.cells_filter = [deepcopy(self.header_row.cells[x]) for x in self.column_names_filter]
            for x in self.header_row.cells_filter:
                x.value = self.column_names_alias[x.value]
        return self.header_row

    def get_visible_rows(self, get_all: bool = False, filter_column: Tuple[str, Any] = None) -> List[TTableRow]:
        """ Return the visible rows """
        x_filter_index = None
        if len(self.data) == 0:
            return self.data

        try:
            x_filter_index = self.column_names.index(filter_column[0])
        except (ValueError, NameError, TypeError):
            pass

        x_end   = len(self.data) if get_all else min(len(self.data), self.m_current_pos + self.visible_items)
        x_start = 0              if get_all else max(0, x_end - self.visible_items)

        # Apply the filter for column layout
        for x_row in self.data[x_start: x_end]:
            if x_filter_index and x_row.cells[x_filter_index].value != filter_column[1]:
                continue
            x_row.cells_filter = [x_row.cells[x] for x in self.column_names_filter] if self.apply_filter else x_row
        return self.data[x_start: x_end]

    def get_child(self) -> TTableRow | None:
        """ Returns the child table, if exists, else None """
        if self.selected_row:
            return self.selected_row.child
        return None

    def do_select(self, row_id: str) -> TTableRow | None:
        """ Select a table row by row index
        :param row_id: The unique row-id
        :return: The row, if exists, else None
        """
        self.selected_row = self.table_index.get(row_id)
        return self.selected_row

    def navigate(self, where_togo: TNavigation = TNavigation.NEXT, position: int = 0) -> None:
        """ Navigate in block mode """
        match where_togo:
            case TNavigation.NEXT:
                self.m_current_pos = max(0, min(len(self.data) - self.visible_items, self.m_current_pos + self.visible_items))
            case TNavigation.PREV:
                self.m_current_pos = max(0, self.m_current_pos - self.visible_items)
            case TNavigation.ABS:
                self.m_current_pos = max(0, min(int(position), len(self) - self.visible_items))
            case TNavigation.TOP:
                self.m_current_pos = 0
            case TNavigation.LAST:
                self.m_current_pos = max(0, len(self) - self.visible_items)

    def do_sort(self, column: int | str, reverse: bool = False) -> None:
        """ Toggle sort on a given column index"""
        try:
            # todo: x_sort = lambda row: row[column]
            x_inx = column if type(column) is int else self.column_names.index(column)
            self.m_column_descr[x_inx].sort = TSort.DESCENDING if reverse else TSort.ASCENDING
            super().sort(key=lambda x_row: x_row.cells[x_inx].value, reverse=reverse)
        except (KeyError, IndexError):
            return

    def print(self) -> None:
        """ Print ASCII formatted table """
        x_column_descr = [self.m_column_descr[x] for x in self.column_names_filter] if self.apply_filter else self.m_column_descr

        print(f'Table: {self.title}')
        x_formatted_row = '|'.join([' {{:<{}}} '.format(x_col.width).format(x_col.filter) for x_col in x_column_descr])  if self.apply_filter else (
                          '|'.join([' {{:<{}}} '.format(x_col.width).format(x_col.header) for x_col in x_column_descr]))

        print(f'|{x_formatted_row}|')

        for x_row in self.get_visible_rows():
            x_cells        = [x_row.cells[x] for x in self.column_names_filter] if self.apply_filter else x_row.cells
            x_row_descr    = zip(x_cells, x_column_descr)
            x_format_descr = [(x_descr.type, x_descr.width, x_cell.value)
                              if  x_descr.type    in self.format_types else ('str', x_descr.width, str(x_cell.value))
                              for x_cell, x_descr in x_row_descr]

            x_formatted_row = '|'.join([self.format_types[x_type](x_width, x_value) for x_type, x_width, x_value in x_format_descr])
            print(f'|{x_formatted_row}|')


def test_table():
    x_path  = Path.cwd()
    x_table = TTable(column_names=['File', 'Size', 'Access'], visible_items=1000)
    for x_item in x_path.iterdir():
        x_stat = os.stat(x_item.name)
        x_time = datetime.fromtimestamp(x_stat.st_atime, tz=timezone.utc)
        x_table.append([str(x_item.name), x_stat.st_size, x_time], attrs={'path': x_item}, row_id=x_item.name)

    # Check if row_id works: These entries should be rejected
    for x_item in x_path.iterdir():
        x_stat = os.stat(x_item.name)
        x_time = datetime.fromtimestamp(x_stat.st_atime, tz=timezone.utc)
        x_table.append([str(x_item.name), x_stat.st_size, x_time], attrs={'path': x_item}, row_id=x_item.name)

    print(f'table header = {[x.value for x in x_table.get_header_row().cells]}')

    print('\n--- natural display')
    x_table.print()

    print('\n--- Filter columns for output')
    x_table.filter_columns([('Size', 'Größe'), ('File', 'Datei')])
    x_table.print()

    print('\n--- Sort for column Size')
    x_table.apply_filter = False
    x_table.do_sort('Size')
    x_table.print()

    print('\n--- Restrict number of items')
    x_table.visible_items = 5
    x_table.print()

    print('\n--- Navigate to next')
    x_table.navigate(where_togo=TNavigation.NEXT)
    x_table.print()


if __name__ == '__main__':
    test_table()
