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
import  os
import  re
from    collections.abc  import Callable
from    collections import UserList
from    dataclasses import dataclass
from    itertools   import filterfalse, chain
from    typing      import List, Dict, NewType, Any
from    enum        import Enum
from    pathlib     import Path
from    datetime    import datetime, timezone
from    copy        import deepcopy
from    service     import TService
from    threading   import Condition, Lock
import  logging
import  sqlite3


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
    ABS         = 0, 'Request an absolute position in the dataset'
    NEXT        = 1, 'Set the cursor to show the next chunk of rows'
    PREV        = 2, 'Set the cursor to show the previous chunk of rows'
    TOP         = 3, 'Set the cursor to the first row'
    LAST        = 4, 'Set the cursor to show the last chunk of rows'


class TSort(Enum):
    """ Sorting control enum to define sort on columns """
    NONE        = 0
    ASCENDING   = 1
    DESCENDING  = 2


@dataclass(kw_only=True)
class TTableCell:
    """ The cell is the smallest unit of a table. This class is a dataclass, so all
    parameters become properties

    :param value: Display value of a cell
    :param type:  Type of the value used for format output.
    :param attrs: User defined attributes
    """
    name:       str                 #: :meta private: - Name of the column
    value:      Any                 #: :meta private: - Value of the cell
    width:      int     = 10        #: :meta private:
    index:      int     = 0         #: :meta private:
    type:       str     = 'str'     #: :meta private:
    attrs:      dict    = None      #: :meta private:


@dataclass(kw_only=True)
class TTableColumn:
    """ Summarize the cell properties in a column, which includes sorting and formatting.
    This class is a dataclass, so all parameters become properties.

    :param alias:   Visible name for output
    :param type:    Value type (class name)
    """
    header:     str                 #: :meta private: Property - Name of the column
    attrs:      dict    = None      #: :meta private: Property - Customizable attributes of the column
    index:      int     = 0         #: :meta private:
    width:      int     = 10        #: :meta private:
    alias:      str     = ''        #: :meta private:
    sort:       bool    = True      #: :meta private:
    type:       str     = 'str'     #: :meta private:
    filter:     str     = None      #: :meta private:
    """  :meta private: """


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
    row_filter: str        = None
    cells: List[TTableCell] | List[str]  #: Property - A list of strings are converted to a list of TTableCells
    cells_filter: List[TTableCell] | None = None  #: :meta private:
    column_descr: List[str] = None      #: :meta private:
    index:      int         = None      #: :meta private:
    row_id:     str         = None      #: :meta private:
    child:      TTable      = None      #: :meta private:
    type:       str         = 'body'    #: :meta private:
    attrs:      dict        = None      #: :meta private:

    def __post_init__(self):
        """ Create a row, converting the values to :py:obj:`eezz.table.TTableCell` """
        if type(self.cells) is List[str]:
            self.cells = [TTableCell(name=str(x), value=str(x)) for x in self.cells]

        self.column_descr = [x.name for x in self.cells]

        if self.attrs:
            for x, y in self.attrs.items():
                setattr(self, x, y)

    def get_values_list(self, index: int | None = None) -> list:
        """ Get all values in a row as a list

        :return: value of each cell
        :rtype:  List[any]
        """
        if index is None:
            return [x.value for x in self.cells]
        else:
            return [index] + [x.value for x in self.cells]

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
        >>> format_types['iban'] = lambda x_size, x_val: ' '.join(['{}' for x in range(6)]).format(* re.findall('.{1,4}', iban))
        de12 1234 1234 1234 1234 12
    """
    column_names:       List[str]                   #: Property - List of column names
    title:              str         = 'Table'       #: Property - Table title name
    column_names_map:   Dict[str, TTableCell] | None = None  #: :meta private:
    column_names_alias: Dict[str, str]  | None  = None  #: :meta private:
    column_names_filter: List[int]      | None  = None  #: :meta private:
    column_descr:       List[TTableColumn]      = None  #: :meta private:
    table_index:        Dict[str, TTableRow]    = None  #: :meta private:
    attrs:              dict        = None          #: :meta private:
    visible_items:      int         = 20            #: :meta private:
    offset:             int         = 0             #: :meta private:
    selected_row:       TTableRow   = None          #: :meta private:
    header_row:         TTableRow   = None          #: :meta private:
    apply_filter_column: bool       = False         #: :meta private:
    format_types:       dict        = None          #: :meta private:
    async_condition:    Condition   = Condition()   #: :meta private:
    async_lock:         Lock        = Lock()        #: :meta private:
    database_path:      str         = ':memory:'    #: :meta private:
    row_filter:         List[List]  = None          #: :meta private:

    def __post_init__(self):
        """ Post init for a data class
        The value for self.format_types could be customized for own data type formatting
        The formatter sends size aad value of the column and receives the formatted string """
        super().__init__()
        self.table_index = dict()

        if not self.column_descr:
            self.column_descr = [TTableColumn(index=x_inx, header=x_str, alias=x_str, width=len(x_str), sort=False)
                                 for x_inx, x_str in enumerate(self.column_names)]
        x_cells = [TTableCell(name=x_str, value=x_str, index=x_inx, width=len(x_str))
                   for x_inx, x_str in enumerate(self.column_names)]

        self.header_row = TTableRow(cells=x_cells, type='header')
        self.column_names_map = {x.value: x for x in x_cells}
        self.keys_syntax = re.compile('[.a-zA-Z0-9]+')

        if not self.format_types:
            self.format_types = {
                'int':      lambda x_size, x_val: ' {{:>{}}} '.format(x_size).format(x_val),
                'str':      lambda x_size, x_val: ' {{:<{}}} '.format(x_size).format(x_val),
                'float':    lambda x_size, x_val: ' {{:>{}.2}} '.format(x_size).format(x_val),
                'datetime': lambda x_size, x_val: ' {{:>{}}} '.format(x_size).format(x_val.strftime("%m/%d/%Y, %H:%M:%S"))}

    def get_column(self, column_name: str) -> TTableColumn | None:
        return next(filterfalse(lambda x_cd: x_cd.header != column_name, self.column_descr), None)

    def filter_clear(self):
        """ Clear the filters and return to original output """
        self.apply_filter_column = False

    def filter_columns(self, column_names: Dict[str, str]) -> None:
        """ The column_names is a dictionary with a set of keys as subset of TTable.column_names.
        The values are translated names to display in output. The order of the keys represents the order in the
        output.
        The filter is used to generate customized output. This function could also be used to reduce the number of
        visible columns

        :param column_names:    Map new names to a column, e.g. after translation
        :type  column_names:    Dict[str: str]

        Example:

        >>> my_table = TTable(column_names=['FileName', 'Size'])
        >>> my_table.filter_columns(column_names={'Size':'Größe', 'FileName': 'Datei'})
        >>> my_table.append(['.idea',        4096])
        >>> my_table.append(['directory.py', 1886])
        >>> my_table.append(['__init__.py',    37])
        >>> my_table.append(['__pycache__',  4096])
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
        self.column_names_alias = column_names
        for x, y in column_names.items():
            try:
                x_inx = self.column_names_map[x].index
                self.column_names_filter.append(x_inx)
                self.column_descr[x_inx].alias = y
                self.apply_filter_column = True
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
        x_inx = len(self.data)
        x_row_descr = list(zip(table_row, self.column_descr))

        # Check for a valid row_id
        if row_id == '':
            row_id = str(x_inx)

        if x_inx == 0:
            self.table_index.clear()
            for x_cell, x_descr in x_row_descr:
                x_descr.type = type(x_cell).__name__

        # Check if the row-id is unique
        if self.table_index.get(row_id):
            if not exists_ok:
                raise TTableInsertException(f'row-id exists {row_id}')
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

    def get_next_values(self, search_filter: Callable[[TTableRow], bool]) -> tuple:
        """  Generator for all elements of a table.
        Restrict the rows for a search attribute, if specified
        Remove the search criteria, if now second visit is required

        :param search_filter: A function that returns True for any row entry to be shown
        :return: Tuple of values in the order of column names
        """
        x_row: TTableRow
        for x_row in self.data:
            if search_filter(x_row):
                yield tuple(x_value for x_value in x_row.get_values_list())

    def do_select(self, get_all: bool = False, filter_descr: list | None = None) -> list | None:
        logger      = logging.getLogger()
        x_database  = sqlite3.connect(self.database_path)
        x_cursor    = x_database.cursor()
        x_ty_map    = {'int': 'integer', 'str': 'text', 'float': 'real'}
        x_create_stm = f"""
            create table {self.title}  
                ({', '.join(f"{x.header} " f"{x_ty_map.get(x.type) 
                    if x_ty_map.get(x.type) else 'text'}" 
                        for x in self.column_descr)}, index_key integer)"""

        logger.debug(msg=f'{x_create_stm}')
        x_cursor.execute(x_create_stm)

        #x_cursor.executemany(f"""
        #    insert into {self.title} values (1, {','.join(['?']*len(self.column_names))}))""",
        #                     [x.get_values_list() for x in self.data] )
        x_cursor.executemany(f"""
            insert into {self.title} values ({('?,' * len(self.column_names))} ?)""",
                             [x.get_values_list() + [i] for i, x in enumerate(self.data)])

        if filter_descr:
            x_where, x_args = self.create_filter(filter_descr)
            x_where_stm  = f"""where {x_where} """
            x_cursor.execute(f"""select * from {self.title} {x_where_stm}""", x_args)
        else:
            x_cursor.execute(f"""select * from {self.title}""")
        return x_cursor.fetchall()

    def create_filter(self, filter_descr: List[List[str]]) -> tuple:
        """ Return a database select where statement

        :param filter_descr: An OR array containing AND arrays. Each AND array is evaluated and then
                            the result is combined to an OR expression. See the following array and the resulting string
                            |br| [['id > 100', 'num < 10], ['id < 10']]
                            |br| '(id > 100 and num < 10) or (id < 10)'
        :return:             A list of values, which meets the filter criteria
        """
        x_where     = list()
        x_args      = list()
        x_or_list   = list()

        for x_or in filter_descr:
            for x_and in x_or:
                x_column_name, x_op, x_value = x_and.split(' ', 2)
                x_where.append(f'{x_column_name} {x_op} ?')
                x_args.append(x_value)
            x_or_list.append(f"""({' and '.join(x_where)})""")
            x_where.clear()
        return ' or '.join(x_or_list), x_args

    def get_visible_rows_1(self, get_all: bool = False) -> List[TTableRow]:
        pass

    def get_visible_rows(self, get_all: bool = False) -> List[TTableRow]:
        """ Select table rows using column values pairs, return at maximum visible_items.
        The value could be any valid regular expression.

        :param get_all:   If True select more than visible_items
        :return:          List of selected rows
        :rtype: List[TTableRow]

        Example:

        >>> my_table = TTable(column_names=['Filename', 'Size'])
        >>> my_table.append(['__init__.py',   37])
        >>> my_table.append(['__pycache__', 4096])
        >>> my_table.append(['main.py',      214])
        >>> my_table.append(['test.py',     1246])
        >>> my_table.get_column().filter = '^__\\w*'
        >>> my_table.print()
        | FileName     | Size |
        | __init__.py  |   37 |
        | __pycache__  | 4096 |

        """
        # in case the filters is a string, we could also handle tree access
        x_filter_row = dict()
        for x in filterfalse(lambda xx_col: not xx_col.filter, self.column_descr):
            x_filter_row.update({x.header: re.compile(x.filter)})

        # columns: List[str], values: List[str]
        # Apply the filter for column layout
        x_count: int  = 0
        x_start: int  = self.offset
        x_match: bool = True

        for x_row in self.data[x_start:]:
            x_match = True
            if x_count > self.visible_items and not get_all:
                break

            for x_key, x_val in x_filter_row.items():
                if not x_val.match(str(x_row[x_key])):
                    x_match = False
                    break

            if not x_match:
                continue

            if self.apply_filter_column:
                x_row.cells_filter = \
                    [x_row.cells[x] for x in self.column_names_filter] if self.apply_filter_column else x_row
            x_count += 1
            yield x_row

    def navigate(self, where_togo: TNavigation = TNavigation.NEXT, position: int = 0) -> None:
        """ Navigate in block mode

        :param where_togo:  Navigation direction
        :type  where_togo:  TNavigation
        :param position:    Position for absolute navigation, ignored in any other case
        """
        match where_togo:
            case TNavigation.NEXT:
                self.offset = max(0, min(len(self.data) - self.visible_items, self.offset + self.visible_items + 1))
            case TNavigation.PREV:
                self.offset = max(0, self.offset - self.visible_items - 1)
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
        logger = logging.getLogger()

        x_column_descr = [self.column_descr[x] for x in
                          self.column_names_filter] if self.apply_filter_column else self.column_descr

        print(f'Table: {self.title}')
        x_formatted_row = '|'.join([' {{:<{}}} '.format(x_col.width).format(x_col.alias) for x_col in
                                    x_column_descr]) if self.apply_filter_column else (
            '|'.join([' {{:<{}}} '.format(x_col.width).format(x_col.header) for x_col in x_column_descr]))

        print(f'|{x_formatted_row}|')
        x_rows_print = rows if rows else self.get_visible_rows()

        for x_row in x_rows_print:
            x_cells = [x_row.cells[x] for x in self.column_names_filter] if self.apply_filter_column else x_row.cells
            x_row_descr = zip(x_cells, x_column_descr)
            x_format_descr = [(x_descr.type, x_descr.width, x_cell.value)
                              if x_descr.type in self.format_types else ('str', x_descr.width, str(x_cell.value))
                              for x_cell, x_descr in x_row_descr]

            x_formatted_row = '|'.join([self.format_types[x_type](x_width, x_value) for x_type, x_width, x_value in x_format_descr])
            print(f'|{x_formatted_row}|')


def test_table():
    """:meta private:"""
    logger.debug(msg="Create a table and read the directory with attribute: [File, Size, Access] and print")
    x_path = Path.cwd()
    x_table = TTable(title= 'list_files', column_names=['File', 'Size', 'Access'], visible_items=1000)
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

    logger.debug(msg='--- Restrict number of visible items')
    x_table.visible_items = 5
    x_table.print()

    logger.debug(msg='--- Navigate to next')
    x_table.navigate(where_togo=TNavigation.NEXT)
    x_table.print()

    x_where, x_args = x_table.create_filter([['Size > 6000'],['File like %py']])
    logger.debug(msg=f"--- where  {x_where}, {x_args}")

    x_result = [x for x in x_table.do_select(get_all=True, filter_descr=[['Size > 6000'],['File like %py']])]
    logger.debug(msg=f'--- result = {x_result}')

    for x in x_result:
        print(f'table row sorted = {x_table[int(x[-1])]}')

    x_result = [x for x in x_table.do_select(get_all=True, filter_descr=[['Size > 6000'],['File like %py']])]
    logger.debug(msg=f'--- result = {x_result}')

    logger.debug(msg='--- Filter *.py')
    x_table.visible_items = 15
    x_table.navigate(where_togo=TNavigation.TOP)
    # x_table.get_column('File').filter = TColumnFilter(rel='=', value=r'\w*.py')
    # x_table.print()


if __name__ == '__main__':
    """:meta private:"""
    x_service = TService(root_path=Path(r'C:\Users\alzer\Projects\github\eezz_full\webroot'))
    x_log_path = x_service.logging_path / 'table.log'
    logging.basicConfig(filename=x_log_path, filemode='w', style='{', format='{name} - {levelname} - {message}')
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)

    # print = logger.debug
    test_table()
