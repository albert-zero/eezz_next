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
import itertools
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
    ABS     = 0, 'Request an absolute position in the dataset'
    NEXT    = 1, 'Set the cursor to show the next chunk of rows'
    PREV    = 2, 'Set the cursor to show the previous chunk of rows'
    TOP     = 3, 'Set the cursor to the first row'
    LAST    = 4, 'Set the cursor to show the last chunk of rows'


class TSort(Enum):
    """ Sorting control enum to define sort on columns """
    NONE    = 0
    ASC     = 1
    DESC    = 2


@dataclass(kw_only=True)
class TTableCell:
    """ The cell is the smallest unit of a table. This class is a dataclass, so all
    parameters become properties

    :param name:  Name of the corresponding column
    :type  name:  str
    :param value: Content of the cell
    :type  type:  str
    """
    name:       str                 #: :meta private: Name of the column
    value:      Any                 #: :meta private: Value of the cell
    width:      int     = 10        #: :meta private: calculated width of a cell
    index:      int     = 0         #: :meta private: calculated index of a cell
    type:       str     = 'str'     #: :meta private: calculated type (could also be user defined)
    attrs:      dict    = None      #: :meta private: user attributes


@dataclass(kw_only=True)
class TTableColumn:
    """ Summarize the cell properties in a column, which includes sorting and formatting.
    This class is a dataclass, so all parameters become properties.

    :param header:  Display name for the column
    :type  header:  str
    :param attrs:   User attributes
    :type  attrs:   dict
    """
    header:     str                 #: :meta private: Name of the column
    attrs:      dict    = None      #: :meta private: Customizable attributes of the column
    index:      int     = 0         #: :meta private: Calculated index of the column
    width:      int     = 10        #: :meta private: Calculated width of the column
    alias:      str     = ''        #: :meta private: Alias name for output
    sort:       bool    = True      #: :meta private: Sort direction of the column
    type:       str     = 'str'     #: :meta private: Type of the column
    filter:     str     = None      #: :meta private: Filter string


# forward declaration
TTable = NewType('TTable', None)


@dataclass(kw_only=True)
class TTableRow:
    """ This structure is created for each row in a table.

    :param cells:   List of row values. If specified as list of string values of the
                    column descriptor are calculated to default.
                    If specified as list of TTableCell you could set specific values during creation
    :type  cells:   List[TTableCell | str]
    """
    cells: List[TTableCell] | List[str]  #: Property - A list of strings are converted to a list of TTableCells
    cells_filter: List[TTableCell] = None  #: :meta private: Filtered cells used for re-ordering and alias names.
    column_descr: List[str] = None      #: :meta private: The column descriptor holds the attributes of the columns
    index:      int         = None      #: :meta private: Unique address for the columns
    row_id:     str         = None      #: :meta private: Unique row id of the row, valid for the entire table
    child:      TTable      = None      #: :meta private: A row could handle recursive data structures
    type:       str         = 'body'    #: :meta private: Customizable type used for triggering template output
    attrs:      dict        = None      #: :meta private: Customizable row attributes

    def __post_init__(self):
        """ Create a row, converting the values to :py:obj:`eezz.table.TTableCell` """
        if type(self.cells) is List[str]:
            self.cells = [TTableCell(name=str(x), value=str(x)) for x in self.cells]

        self.column_descr = [x.name for x in self.cells]

        if self.attrs:
            for x, y in self.attrs.items():
                setattr(self, x, y)

    def get_values_list(self) -> list:
        """ Get all values in a row as a list """
        return [x.value for x in self.cells]

    def __getitem__(self, column: int | str) -> Any:
        """ Allows field access: value = row[column] """
        x_inx = column if type(column) is int else self.column_descr.index(column)
        return self.cells[x_inx].value

    def __setitem__(self, column: int | str, value: Any) -> None:
        """ Allows field access: r[column] = value """
        x_inx = column if type(column) is int else self.column_descr.index(column)
        self.cells[x_inx].value = value


@dataclass(kw_only=True)
class TTable(UserList):
    """ The table is derived from User-list to enable sort and list management
    This is a dataclass, so the arguments become properties

    .. _ttable_parameter_list:

    :param column_names:    List of names for each column
    :type  column_names:    List[str]
    :param title:           Table name and title
    :type  title:           str

    :ivar Dict[str, Callable[size, value]] format_types: A map for types and format rules.
        The callable takes two variables, the width and the value.

    Examples:
        Table instance:

        >>> from table import TTable
        >>> my_table = TTable(column_names=['FileName', 'Size'], title='Directory')
        >>> # for file in Path('.').iterdir():
        >>> #    my_table.append(table_row=[file, file.stat().st_size])
        >>> row = my_table.append(table_row=['.idea',        4096])
        >>> row = my_table.append(table_row=['directory.py', 1699])
        >>> row = my_table.append(table_row=['__init__.py',    37])
        >>> row = my_table.append(table_row=['__pycache__',  4096])
        >>> my_table.print()
        Table: Directory
        | FileName     | Size |
        | .idea        | 4096 |
        | directory.py | 1699 |
        | __init__.py  |   37 |
        | __pycache__  | 4096 |

        This is a possible extension of a format for type iban, breaking the string into chunks of 4:

        >>> iban = 'de1212341234123412'
        >>> my_table.format_types['iban'] = lambda x_size, x_val: f"{(' '.join(re.findall('.{1,4}', x_val))):>{x_size}}"
        >>> print(f"{my_table.format_types['iban'](30, iban)}")
                de12 1234 1234 1234 12
    """
    column_names:       List[str]                       #: :meta private: Property - List of column names
    title:              str         = 'TTable'          #: :meta private: Property - Table title name
    column_names_map:   Dict[str, TTableCell]   = None  #: :meta private: Map name to columns
    column_names_alias: Dict[str, str]          = None  #: :meta private: Translated column names
    column_names_filter: List[int]              = None  #: :meta private: Index for shuffle columns
    column_descr:       List[TTableColumn]      = None  #: :meta private: Describes each column
    table_index:        Dict[str, TTableRow]    = None  #: :meta private: Table unique row index
    attrs:              dict        = None              #: :meta private: User attributes
    visible_items:      int         = 20                #: :meta private: Number of items to show
    offset:             int         = 0                 #: :meta private: Offset for sequence reading
    selected_row:       TTableRow   = None              #: :meta private: Selected row
    header_row:         TTableRow   = None              #: :meta private: Header row of the table
    apply_filter_column: bool       = False             #: :meta private: If true columns are reordered and translated
    format_types:       dict        = None              #: :meta private: Map output format for value type
    async_condition:    Condition   = Condition()       #: :meta private: Used for async access to table
    async_lock:         Lock        = Lock()            #: :meta private: Used for async access to table values
    database_path:      str         = ':memory:'        #: :meta private: The database path
    row_filter_descr:   List[List]  = None              #: :meta private: The row filter combines column values
    is_synchron:        bool        = False             #: :meta private: Used to reduce calls to do_select

    def __post_init__(self):
        """ Post init for a data class
        The value for self.format_types could be customized for own data type formatting
        The formatter sends size aad value of the column and receives the formatted string """
        super().__init__()
        self.table_index = dict()

        if not self.column_descr:
            self.column_descr = [TTableColumn(index=x_inx, header=x_str, alias=x_str, width=len(x_str), sort=False)
                                 for x_inx, x_str in enumerate(self.column_names)]

        x_cells               = [TTableCell(name=x_str, value=x_str, index=x_inx, width=len(x_str)) for x_inx, x_str in enumerate(self.column_names)]
        self.header_row       = TTableRow(cells=x_cells, type='header')
        self.column_names_map = {x_cell.value: x_cell for x_cell in x_cells}

        if not self.format_types:
            self.format_types = {
                'int':      lambda x_size, x_val: ' {{:>{}}} '.format(x_size).format(x_val),
                'str':      lambda x_size, x_val: ' {{:<{}}} '.format(x_size).format(x_val),
                'float':    lambda x_size, x_val: ' {{:>{}.2}} '.format(x_size).format(x_val),
                'datetime': lambda x_size, x_val: ' {{:>{}}} '.format(x_size).format(x_val.strftime("%m/%d/%Y, %H:%M:%S"))}

    def get_column(self, column_name: str) -> TTableColumn | None:
        """:meta private:"""
        return next(filterfalse(lambda x_cd: x_cd.header != column_name, self.column_descr), None)

    def filter_clear(self):
        """:meta private: Clear the filters and return to original output """
        self.apply_filter_column = False

    def filter_rows(self, row_filter_descr: List[List[str]]):
        """ Set the row filter: Each inner list is joined with 'AND'.
        The outer list joins the inner lists with 'OR' """
        self.row_filter_descr = row_filter_descr
        self.is_synchron      = False

    def filter_columns(self, column_names: Dict[str, str]) -> None:
        """ The column_names is a dictionary with a set of keys as subset of TTable.column_names.
        The values are translated names to display in output. The order of the keys represents the order in the
        output.
        The filter is used to generate customized output. This function could also be used to reduce the number of
        visible columns

        :param column_names:    Map new names to a column, e.g. after translation
        :type  column_names:    Dict[str: str]

        Example:

        >>> my_table = TTable(column_names=['FileName', 'Size'], title='Directory')
        >>> my_table.filter_columns(column_names={'Size':'Größe', 'FileName': 'Datei'})
        >>> row = my_table.append(['.idea',        4096])
        >>> row = my_table.append(['directory.py', 1886])
        >>> row = my_table.append(['__init__.py',    37])
        >>> row = my_table.append(['__pycache__',  4096])
        >>> my_table.print()
        Table: Directory
        | Größe | Datei        |
        |  4096 | .idea        |
        |  1886 | directory.py |
        |    37 | __init__.py  |
        |  4096 | __pycache__  |
        """
        # Create a list of column index and a translation of the column header entry
        self.column_names_filter = list()
        self.column_names_alias  = column_names
        for x, y in column_names.items():
            try:
                x_inx = self.column_names_map[x].index
                self.column_names_filter.append(x_inx)
                self.column_descr[x_inx].alias = y
                self.column_descr[x_inx].width = max(len(y), self.column_descr[x_inx].width)
                self.apply_filter_column       = True
            except KeyError:
                pass

    def append(self, table_row: list, attrs: dict = None, row_type: str = 'body', row_id: str = '', exists_ok=False) -> TTableRow:
        """ Append a row into the table
        This procedure also defines the column type and the width

        :param table_row:   List of values
        :type  table_row:   List[str]
        :param attrs:       Customizable attributes
        :param row_type:    Row type used for output filter
        :param row_id:      Unique row id, which consists of pattern letters and numbers [a-zA-Z0-9]+
        :param exists_ok:   Try to append, but do not throw exception, if key exists
        :return:            The generated row object
        :rtype:             TTableRow
        :raise TableInsertException: Exception if row-id already exists
        """
        # define the type with the first line inserted
        x_inx       = len(self.data)
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

    def do_select(self, get_all: bool = False, filter_descr: list = None) -> list:
        """ Create and execute a select statement on the given database.
        For a :memory: database this method achieves a complex search combining multiple columns
        returning the values and the row index of the internal data
        """
        sqlite3.register_adapter(datetime, lambda x_val: x_val.isoformat())
        sqlite3.register_converter("datetime", lambda x_val: datetime.fromisoformat(x_val.decode()))
        self.is_synchron = True

        logger      = logging.getLogger()
        x_database  = sqlite3.connect(self.database_path)
        x_cursor    = x_database.cursor()
        x_ty_map    = {'int': 'integer', 'str': 'text', 'float': 'real'}
        x_options   = '' if get_all else f' limit {self.visible_items} offset {self.offset}'
        x_sorted    = list(itertools.filterfalse(lambda x: not x.sort, self.column_descr))
        x_sort_stm  = ''

        for x in x_sorted:
            x_sort_stm = f' order by {x.header} ASC'
            break

        if self.database_path == ':memory:':
            x_create_stm = f"""create table {self.title}  ({', '.join(f"{x.header}  {x_ty_map.get(x.type)  if x_ty_map.get(x.type) else 'text'}" for x in self.column_descr)}, index_key integer)"""
            logger.debug(msg=f'TTable.do_select: {x_create_stm}')
            x_cursor.execute(x_create_stm)

            x_insert_stm = f"""insert into {self.title} values ({('?,' * len(self.column_names))} ?)"""
            logger.debug(msg=f'TTable.do_select: {x_insert_stm}')

            x_row: TTableRow
            for i, x_row in enumerate(self.data):
                x_cursor.execute(x_insert_stm, tuple(x_row.get_values_list() + [i]))
            x_database.commit()

        if filter_descr:
            x_where, x_args = self.create_filter(filter_descr)
            x_select_stm = f"""select * from {self.title} where {x_where} {x_sort_stm} {x_options}"""
            logger.debug(msg=f'TTable.do_select: {x_select_stm}')
            x_cursor.execute(x_select_stm, tuple(x_args))
        else:
            x_cursor.execute(f"""select * from {self.title} {x_options}""")
        yield from x_cursor.fetchall()

    def create_filter(self, filter_descr: List[List[str]]) -> tuple:
        """ :meta private: Return a database select where statement

        :param filter_descr: An OR array containing AND arrays. Each AND array is evaluated and then
                            the result is combined to an OR expression. See the following array and the resulting string
                |br| list[list['id > 100', 'num < 10], list['id < 10']] ->
                |br| tuple('(id > ? and num < ?) or (id < ?)', tuple('100',10,10))
        :return: Valid SQL part as string for a 'where' clause and the value list
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

    def get_visible_rows(self, get_all: bool = False) -> List[TTableRow]:
        """ Select table rows using column values pairs, return at maximum visible_items.
        The value could be any valid regular expression.

        :param get_all:   If True select more than visible_items
        :return:          List of selected rows
        :rtype: List[TTableRow]

        Example:

        >>> my_table = TTable(column_names=['FileName', 'Size'], title='Directory')
        >>> row = my_table.append(['__init__.py',   37])
        >>> row = my_table.append(['__pycache__', 4096])
        >>> row = my_table.append(['test.py',     1246])
        >>> my_table.get_column('FileName').filter = '^__[a-zA-Z]*__.py'
        >>> my_table.print()
        Table: Directory
        | FileName    | Size |
        | __init__.py |   37 |
        """
        # in case the filters is a string, we could also handle tree access
        if self.row_filter_descr and not self.is_synchron:
            for x_selected in self.do_select(get_all=get_all, filter_descr=self.row_filter_descr):
                yield self.data[x_selected[-1]]
            return None

        x_filter_row = dict()
        for x in filterfalse(lambda xx_col: not xx_col.filter, self.column_descr):
            x_filter_row.update({x.header: re.compile(x.filter)})

        # columns: List[str], values: List[str]
        # Apply the filter for column layout
        x_count: int  = 0
        x_start: int  = self.offset

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
        self.is_synchron = False

    def do_sort(self, column: int | str, reverse: bool = False) -> None:
        """ :meta private: Toggle sort on a given column index """
        super().sort(key=lambda x_row: x_row[column], reverse=reverse)

    def print(self) -> None:
        """ Print ASCII formatted table

        :param rows: Optional parameter to print selected rows. If not set, print the visible rows.
        """
        x_column_descr  = [self.column_descr[x] for x in self.column_names_filter] if self.apply_filter_column \
                      else self.column_descr

        print(f'Table: {self.title}')
        x_formatted_row = '|'.join([' {{:<{}}} '.format(x_col.width).format(x_col.alias)  for x_col in x_column_descr])  if self.apply_filter_column \
                    else ('|'.join([' {{:<{}}} '.format(x_col.width).format(x_col.header) for x_col in x_column_descr]))
        print(f'|{x_formatted_row}|')

        for x_row in self.get_visible_rows():
            x_cells         = [x_row.cells[x] for x in self.column_names_filter] if self.apply_filter_column else x_row.cells
            x_row_descr     = zip(x_cells, x_column_descr)
            x_format_descr  = [(x_descr.type, x_descr.width,     x_cell.value) if x_descr.type in self.format_types
                          else ('str',        x_descr.width, str(x_cell.value)) for x_cell, x_descr in x_row_descr]

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

    # x_result = [x for x in x_table.do_select(get_all=True, filter_descr=[['Size > 20000'],['File like %py']])]
    x_result = [x for x in x_table.do_select(get_all=True, filter_descr=[["Size > 10000"],["File like %.py"]])]
    logger.debug(msg=f'--- result = {x_result}')

    x_table.visible_items = 100
    x_table.filter_rows([["Size > 10000" ],["File like %.py"]])
    x_table.print()


if __name__ == '__main__':
    """:meta private:"""
    x_service = TService(root_path=Path(r'C:\Users\alzer\Projects\github\eezz_full\webroot'))
    x_log_path = x_service.logging_path / 'table.log'
    logging.basicConfig(filename=x_log_path, filemode='w', style='{', format='{name} - {levelname} - {message}')
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)

    # print = logger.debug
    test_table()
