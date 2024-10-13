#!/usr/bin/python3
"""
    This module implements the following classes:

    * :py:class:`eezz.table.TTableCell`:   Defines properties of a table cell
    * :py:class:`eezz.table.TTableRow`:    Defines properties of a table row, containing a list of TTableCells
    * :py:class:`eezz.table.TTableColumn`: Defines properties of a table column
    * :py:class:`eezz.table.TTable`:       Defines properties of a table, containing a list of TTableRows
    * :py:class:`eezz.table.TTableInsertException`: Exception on checking the row-id, which has to be unique

    TTable is used for formatted ASCII output of a table structure.
    It allows to access the table data for further processing e.g. for HTML output. The class handles an
    internal read cursor, which allows to navigate in the list of rows and to read a fixed amount of rows.

    TTable is a list of TTableRow objects, each of which is a list of TCell objects.
    The TTableColumn holds the as well column names as types and is used to organize sort and filter.
    A TTableCell object could hold a TTable object for recursive tree structures.

    Besides this the following enumerations are used

    * :py:class:`eezz.table.TNavigation`:   Enumeration for method :py:meth:`eezz.table.TTable.navigate`
    * :py:class:`eezz.table.TSort`:         Enumeration for method :py:meth:`eezz.table.TTable.do_sort`

"""
import os
import re
from   collections  import UserList
from   dataclasses  import dataclass
from   typing       import List, Dict, NewType, Any
from   enum         import Enum
from   pathlib      import Path
from   datetime     import datetime, timezone
from   copy         import deepcopy
from   service      import TService
from   threading    import Condition, Lock
import logging


class TTableInsertException(Exception):
    """ The table exception: trying to insert a double row-id
    """
    def __init__(self, message: str = "entry already exists, row-id has to be unique"):
        super().__init__(message)


class TNavigation(Enum):
    """ Elements to describe navigation events for method :py:func:`eezz.table.TTable.navigate`. The navigation is
    organized in chunks of rows given by property
    :ref:`TTable.visible_items <ttable_parameter_list>`:

    """
    ABS  = 0, 'Request an absolute position in the dataset'
    NEXT = 1, 'Set the cursor to show the next chunk of rows'
    PREV = 2, 'Set the cursor to show the previous chunk of rows'
    TOP  = 3, 'Set the cursor to the first row'
    LAST = 4, 'Set the cursor to show the last chunk of rows'


class TSort(Enum):
    """ Sorting control enum to define sort on columns """
    NONE       = 0
    ASCENDING  = 1
    DESCENDING = 2


@dataclass(kw_only=True)
class TTableCell:
    """ The cell is the smallest unit of a table. This class is a dataclass, so all
    parameters become properties

    :ivar int  width: Width of the cell content
    :ivar int  value: Display value of the cell
    :ivar int  index: Index of this cell in the column
    :ivar str  type:  Type of the value (class name), derived from runtime environment
    :ivar dict attrs: User defined attributes
    """
    name:   str             #: Property - Name of the column
    value:  Any             #: Property - Value of the cell
    width:  int  = 10       #: :meta private:
    index:  int  = 0        #: :meta private:
    type:   str  = 'str'    #: :meta private:
    attrs:  dict = None     #: :meta private:


@dataclass(kw_only=True)
class TTableColumn:
    """ Summarize the cell properties in a column, which includes sorting and formatting.
    This class is a dataclass, so all parameters become properties.

    :ivar int   index:   Stable address the column, even if filtered or translated
    :ivar int   width:   Width to fit the largest element in the column
    :ivar str   filter:  Visible name for output
    :ivar TSort sort:    Sort direction
    :ivar str   type:    Value type (class name)
    """
    header: str             #: Property - Name of the column
    attrs:  dict = None     #: Property - Customizable attributes of the column
    index:  int  = 0        #: :meta private:
    width:  int  = 10       #: :meta private:
    filter: str  = ''       #: :meta private:
    sort:   bool = True     #: :meta private:
    type:   str  = ''       #: :meta private:


# forward declaration
TTable = NewType('TTable', None)


@dataclass(kw_only=True)
class TTableRow:
    """ This structure is created for each row in a table. It allows also to specify a sub-structure table.
    This class is a dataclass, so all parameters become properties
    TTable row implements methods to access values like an array

    * __getitem__ : value = row[column-name]
    * __setitem__ : row[column-name] = value

    :ivar List[str] cells_filter:    A list of cells with filtered attributes. Used for example for translation or re-ordering.
    :ivar List[str] column_descr:    The column descriptor holds the name of the column
    :ivar int       index:           Unique address for the column
    :ivar str       row_id:          Unique row id for the entire table
    :ivar TTable    child:           The row could handle recursive data structures
    :ivar str       type:            Customizable type used for triggering template output
    :ivar dict      attrs:           Customizable row attributes
    """
    cells:        List[TTableCell] | List[str]      #: Property - A list of strings are converted to a list of TTableCells
    cells_filter: List[TTableCell] | None = None    #: :meta private:
    column_descr: List[str]   = None                #: :meta private:
    index:        int         = None                #: :meta private:
    row_id:       str         = None                #: :meta private:
    child:        TTable      = None                #: :meta private:
    type:         str         = 'body'              #: :meta private:
    attrs:        dict        = None                #: :meta private:

    def __post_init__(self):
        """ Create a row, converting the values to :py:obj:`eezz.table.TTableCell` """
        if type(self.cells) is List[str]:
            self.cells = [TTableCell(name=str(x), value=str(x)) for x in self.cells]

        self.column_descr = [x.name for x in self.cells]

        if self.attrs:
            for x, y in self.attrs.items():
                setattr(self, x, y)

    def get_values_list(self) -> list:
        """ Get all values in a row as a list

        :return: value of each cell
        :rtype:  List[any]
        """
        return [x.value for x in self.cells]

    def __getitem__(self, column: int | str) -> Any:
        """ Allows field access: value = row[column]

        :param column:  Specify the cell either by index or name
        :return:        The value of the cell
        """
        x_inx = column if type(column) is int else self.column_descr.index(column)
        return self.cells[x_inx].value

    def __setitem__(self, column: int | str, value: Any) -> None:
        """ Allows field access: r[column] = value

        :param column:  Specify the cell either by index or name
        :param value:   Value to set
        """
        x_inx = column if type(column) is int else self.column_descr.index(column)
        self.cells[x_inx].value = value


@dataclass(kw_only=True)
class TTable(UserList):
    """ The table is derived from User-list to enable sort and list management
    This is a dataclass, so the arguments become properties

    .. _ttable_parameter_list:

    :ivar Dict[str, TTableCell] column_names_map:    Map names for output to re-arrange order
    :ivar Dict[str, str]        column_names_alias:  Map alias names to column names. This could be used to translate the table header\
    without changing the select statements.
    :ivar List[int]             column_names_filter: Map columns for output, allows selecting a subset and rearanging, without touching\
    the internal structure of the table
    :ivar List[TTableColumn]    column_descr:        Contains all attributes of a column like type and width
    :ivar Dict[str, TTableRow]  table_index:         Managing an index for row-id
    :ivar int                   visible_items:       Number of visible items: default is 20
    :ivar int                   offset:              Cursor position in data set
    :ivar TTableRow             header_row:          Header row
    :ivar bool                  apply_filter_column: Choose between a filtered setup or the original header
    :ivar Dict[str, Callable]   format_types:        Maps a column type to a formatter for ASCII output

    Examples:
        Table instance:

        >>> from table import TTable
        >>> my_table = TTable(column_names=['FileName', 'Size'], title='Directory')
        >>> for file in Path('.').iterdir():
        >>>     my_table.append(table_row=[file, file.stat().st_size])
        >>> my_table.print()
        Table: Directory
        | FileName     | Size |
        | .idea        | 4096 |
        | directory.py | 1699 |
        | __init__.py  |   37 |
        | __pycache__  | 4096 |

        This is a possible extension of a format for type iban, breaking the string into chunks of 4:

        >>> iban = 'de1212341234123412'
        >>> format_types['iban'] = lambda x_size, x_val: ' '.join(['{}' for x in range(6)]).format(* re.findall('.{1,4}', iban)})
        de12 1234 1234 1234 1234 12
    """
    column_names:           List[str]                               #: Property - List of column names
    title:                  str  = 'Table'                          #: Property - Table title name
    column_names_map:       Dict[str, TTableCell] | None = None     #: :meta private:
    column_names_alias:     Dict[str, str]        | None = None     #: :meta private:
    column_names_filter:    List[int]             | None = None     #: :meta private:
    column_descr:           List[TTableColumn]    = None            #: :meta private:
    table_index:            Dict[str, TTableRow]  = None            #: :meta private:
    attrs:                  dict        = None                      #: :meta private:
    visible_items:          int         = 20                        #: :meta private:
    offset:                 int         = 0                         #: :meta private:
    selected_row:           TTableRow   = None                      #: :meta private:
    header_row:             TTableRow   = None                      #: :meta private:
    apply_filter_column:    bool        = False                     #: :meta private:
    format_types:           dict        = None                      #: :meta private:
    async_condition:        Condition   = Condition()               #: :meta private:
    async_lock:             Lock        = Lock()                    #: :meta private:

    def __post_init__(self):
        """ Post init for a data class
        The value for self.format_types could be customized for own data type formatting
        The formatter sends size aad value of the column and receives the formatted string """
        super().__init__()
        self.table_index      = dict()
        self.column_descr     = [TTableColumn(index=x_inx, header=x_str, filter=x_str, width=len(x_str), sort=False) for x_inx, x_str in enumerate(self.column_names)]
        x_cells               = [TTableCell(name=x_str, value=x_str, index=x_inx, width=len(x_str)) for x_inx, x_str in enumerate(self.column_names)]
        self.header_row       = TTableRow(cells=x_cells, type='header')
        self.column_names_map = {x.value: x for x in x_cells}
        self.keys_syntax       = re.compile('[a-zA-Z0-9]+')
        if not self.format_types:
            self.format_types = {
                'int':      lambda x_size, x_val: ' {{:>{}}} '.format(x_size).format(x_val),
                'str':      lambda x_size, x_val: ' {{:<{}}} '.format(x_size).format(x_val),
                'float':    lambda x_size, x_val: ' {{:>{}.2}} '.format(x_size).format(x_val),
                'datetime': lambda x_size, x_val: ' {{:>{}}} '.format(x_size).format(x_val.strftime("%m/%d/%Y, %H:%M:%S"))}

    def filter_clear(self):
        """ Clear the filters and return to original output """
        self.apply_filter_column  = False

    def filter_columns(self, column_names: Dict[str, str]) -> None:
        """ The column_names is a dictionary with a set of keys as subset of TTable.column_names.
        The values are translated names to display in output. The order of the keys represents the order in the
        output.
        The filter is used to generate customized output. This function could also be used to reduce the number of
        visible columns

        :param column_names:    Map new names to a column, e.g. after translation
        :type  column_names:    Dict[column_name: alias_name]

        Example:

        >>> my_table.filter_columns(column_names={'Size':'Größe', 'FileName': 'Datei'})
        >>> my_table.print()
        Table: Directory
        | Größe | Datei        |
        | 4096  | .idea        |
        | 1886  | directory.py |
        |   37  | __init__.py  |
        | 4096  | __pycache__  |
        """
        # Create a list of column index and a translation of the column header entry
        self.column_names_filter = list()
        self.column_names_alias  = column_names
        for x, y in column_names.items():
            try:
                x_inx = self.column_names_map[x].index
                self.column_names_filter.append(x_inx)
                self.column_descr[x_inx].filter = y
                self.apply_filter_column        = True
            except KeyError:
                pass

    def append(self, table_row: list, attrs: dict = None, row_type: str = 'body', row_id: str = '', exists_ok=True) -> TTableRow:
        """ Append a row into the table
        This procedure also defines the column type and the width

        :param exists_ok:   Try to append, but do not throw exception, if key exists
        :param table_row:   List of values
        :param attrs:       Customizable attributes
        :param row_type:    Row type used for output filter
        :param row_id:      Unique row id, which consists of pattern letters and numbers [a-zA-Z0-9]+
        :raise TableInsertException: Exception if row-id already exists
        """
        # define the type with the first line inserted
        x_inx       = len(self.data)
        x_row_descr = list(zip(table_row, self.column_descr))

        # Check for a valid row_id
        if row_id == '':
            row_id = str(x_inx)
        else:
            if x := self.keys_syntax.match(row_id):
                row_id = x[0]
            else:
                raise TTableInsertException('invalid row-id')

        if x_inx == 0:
            self.table_index.clear()
            for x_cell, x_descr in x_row_descr:
                x_descr.type = type(x_cell).__name__

        # Check if the row-id is unique
        if self.table_index.get(row_id):
            if not exists_ok:
                raise TTableInsertException('row-id exists')
            return self.table_index.get(row_id)

        x_cells = [TTableCell(name=x_descr.header, width=len(str(x_cell)), value=x_cell, index=x_descr.index, type=x_descr.type) for x_cell, x_descr in x_row_descr]
        x_row   = TTableRow(index=x_inx, cells=x_cells, attrs=attrs, type=row_type, row_id=row_id, column_descr=self.column_names)

        super(UserList, self).append(x_row)
        self.table_index[row_id] = x_row

        for x_cell, x_descr in x_row_descr:
            x_descr.width = max(len(str(x_cell)), x_descr.width)
        return x_row

    def get_header_row(self) -> TTableRow:
        """ Returns the header row.

        :return: The header of the table
        :rtype:  TTableRow
        """
        if self.apply_filter_column:
            # Select the visible columns in the desired order and map the new names
            self.header_row.cells_filter = [deepcopy(self.header_row.cells[x]) for x in self.column_names_filter]
            for x in self.header_row.cells_filter:
                x.value = self.column_names_alias[x.value]
        return self.header_row

    def get_visible_rows(self, get_all: bool = False) -> List[TTableRow]:
        """ Return the visible rows at the current cursor

        :param get_all:     A bool value to overwrite the visible_items for the current call
        :return:            A list of visible row items
        """
        if len(self.data) == 0:
            return self.data

        x_end    = len(self.data) if get_all else min(len(self.data), self.offset + self.visible_items)
        x_start  = 0              if get_all else max(0, x_end - self.visible_items)
        x_filter_results = list()

        # Apply the filter for column layout
        for i, x_row in enumerate(self.data[x_start:]):
            if self.apply_filter_column:
                x_row.cells_filter = [x_row.cells[x] for x in self.column_names_filter] if self.apply_filter_column else x_row

            x_filter_results.append(x_row)
            if len(x_filter_results) >= self.visible_items:
                self.offset = x_start + i + 1
                break
        return x_filter_results

    def do_select(self, filters: dict | str, get_all: bool = False) -> List[TTableRow]:
        """ Select table rows using column values pairs, return at maximum visible_items.
        The value could be any valid regular expression.

        :param filters:   dictionary with column-name/value pairs \
        or qualified string: row-id[.row-id]*, in which case the algorithm will search recursivly \
        in TTableRow.child structure
        :type filters:    Dict[column_name, value] or qualified string
        :param get_all:   If True select more than visible_items
        :return:          List of selected rows
        :rtype: List[TTableRow]

        Example:

        >>> rows  = my_table.do_select(filters={'FileName': '__*'})
        >>> my_table.print(rows)
        | FileName     | Size |
        | __init__.py  |   37 |
        | __pycache__  | 4096 |

        """
        # in case the filters is a string, we could also handle tree access
        if isinstance(filters, str):
            x_ids = filters.split('.')
            x_row = self.table_index.get(x_ids[0])
            for x_id in x_ids[1:]:
                node: TTable = x_row.child
                if not node:
                    break
                x_row = node.table_index.get(x_id)
            return [x_row]

        # columns: List[str], values: List[str]
        x_filter_results = list()
        x_filter = {x: re.compile(y) for x, y in filters.items()}

        # Apply the filter for column layout
        for x_row in self.data:
            try:
                x_res = [x for x in filters.keys() if x_filter[str(x)].match(str(x_row[x]))]
                # combine result for all conditions are matched as true
                if len(x_res) == len(x_filter):
                    if self.apply_filter_column:
                        x_row.cells_filter = [x_row.cells[x] for x in self.column_names_filter] if self.apply_filter_column else x_row
                    x_filter_results.append(x_row)
                if len(x_filter_results) >= self.visible_items and not get_all:
                    break
            except (KeyError, ValueError) as x_ex:
                pass
        return x_filter_results

    def navigate(self, where_togo: TNavigation = TNavigation.NEXT, position: int = 0) -> None:
        """ Navigate in block mode

        :param where_togo:  Navigation direction
        :type  where_togo:  TNavigation
        :param position:    Position for absolute navigation, ignored in any other case
        """
        match where_togo:
            case TNavigation.NEXT:
                self.offset = max(0, min(len(self.data) - self.visible_items, self.offset + self.visible_items))
            case TNavigation.PREV:
                self.offset = max(0, self.offset - self.visible_items)
            case TNavigation.ABS:
                self.offset = max(0, min(int(position), len(self) - self.visible_items))
            case TNavigation.TOP:
                self.offset = 0
            case TNavigation.LAST:
                self.offset = max(0, len(self) - self.visible_items)

    def do_sort(self, column: int | str, reverse: bool = False) -> None:
        """ Toggle sort on a given column index

        :param column:      The column to sort for
        :param reverse:     Sort direction reversed
        """
        super().sort(key=lambda x_row: x_row[column], reverse=reverse)

    def print(self, rows: List[TTableRow] | None = None) -> None:
        """ Print ASCII formatted table

        :param rows: Optional parameter to print selected rows. If not set, print the visible rows.
        """
        x_column_descr = [self.column_descr[x] for x in self.column_names_filter] if self.apply_filter_column else self.column_descr

        print(f'Table: {self.title}')
        x_formatted_row = '|'.join([' {{:<{}}} '.format(x_col.width).format(x_col.filter) for x_col in x_column_descr])  if self.apply_filter_column else (
                          '|'.join([' {{:<{}}} '.format(x_col.width).format(x_col.header) for x_col in x_column_descr]))

        print(f'|{x_formatted_row}|')
        x_rows_print = rows if rows else self.get_visible_rows()

        for x_row in x_rows_print:
            x_cells        = [x_row.cells[x] for x in self.column_names_filter] if self.apply_filter_column else x_row.cells
            x_row_descr    = zip(x_cells, x_column_descr)
            x_format_descr = [(x_descr.type, x_descr.width, x_cell.value)
                              if  x_descr.type    in self.format_types else ('str', x_descr.width, str(x_cell.value))
                              for x_cell, x_descr in x_row_descr]

            x_formatted_row = '|'.join([self.format_types[x_type](x_width, x_value) for x_type, x_width, x_value in x_format_descr])
            print(f'|{x_formatted_row}|')


def test_table():
    """:meta private:"""
    logger.debug(msg="Create a table and read the directory with attribute: [File, Size, Access] and print")
    x_path  = Path.cwd()
    x_table = TTable(column_names=['File', 'Size', 'Access'], visible_items=1000)
    for x_item in x_path.iterdir():
        x_stat = os.stat(x_item.name)
        x_time = datetime.fromtimestamp(x_stat.st_atime, tz=timezone.utc)
        x_table.append([str(x_item.name), x_stat.st_size, x_time], attrs={'path': x_item}, row_id=x_item.name)

    # Check if row_id works: These entries should be rejected
    for x_item in x_path.iterdir():
        try:
            x_stat = os.stat(x_item.name)
            x_time = datetime.fromtimestamp(x_stat.st_atime, tz=timezone.utc)
            x_table.append([str(x_item.name), x_stat.st_size, x_time], attrs={'path': x_item}, row_id=x_item.name)
        except TTableInsertException as x_except:
            logger.debug(msg='Check row-id: Add entries with same row-id should be rejected')
            logger.debug(msg=f'TableInsertException {x_item.name}: {x_except}')
            break

    logger.debug(msg=f'table header = {[x.value for x in x_table.get_header_row().cells]}')
    x_table.print()

    logger.debug(msg="--- Output restricted on File and Size, change the position and translate the column names")
    x_table.filter_columns({'Size': 'Größe', 'File': 'Datei'})
    x_table.print()

    logger.debug(msg='--- Sort for column Size')
    x_table.apply_filter_column = False
    x_table.do_sort('Size')
    x_table.print()

    logger.debug(msg='--- Restrict number of items')
    x_table.visible_items = 5
    x_table.print()

    logger.debug(msg='--- Navigate to next')
    x_table.navigate(where_togo=TNavigation.NEXT)
    x_table.print()

    logger.debug(msg='--- Filter *.py')
    x_table.visible_items = 25
    x_table.navigate(where_togo=TNavigation.TOP)
    x_table.print()


def test_database_filter() -> None:
    """:meta private:
    TTable.get_visible_rows takes a row filter
    A row is inserted, if the returned value is not None, which is the default.
    For a database filter the column name and value are used for select statement. Example: 'where File like %.py'
    """
    pass


if __name__ == '__main__':
    """:meta private:"""
    x_service  = TService(root_path=Path(r'C:\Users\alzer\Projects\github\eezz_full\webroot'))
    x_log_path = x_service.logging_path / 'app.log'
    logging.basicConfig(filename=x_log_path, filemode='w', style='{', format='{name} - {levelname} - {message}')
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)

    # print = logger.debug
    test_table()
