#!/usr/bin/python3
"""
    This module implements the following classes:

    * :py:class:`eezz.table.TTableCell`:   Defines properties of a table cell
    * :py:class:`eezz.table.TTableRow`:    Defines properties of a table row, containing a list of TTableCells
    * :py:class:`eezz.table.TTableColumn`: Defines properties of a table column
    * :py:class:`eezz.table.TTable`:       Defines properties of a table, containing a list of TTableRows
    * :py:class:`eezz.table.TTableException`: Exception on checking the row-id, which has to be unique

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
import  itertools
import  os
import  io
import  re
import  sys
from    collections.abc  import Callable
from    collections import UserList
from    dataclasses import dataclass, field
from    itertools   import filterfalse, chain
from    typing      import List, Dict, NewType, Any, ClassVar
from    enum        import Enum
from    pathlib     import Path
from    datetime    import datetime, timezone
from    copy        import deepcopy
from    abc         import abstractmethod
from    Crypto.Hash import SHA1

from typing_extensions import override

from    service     import TService
from    threading   import Condition, Lock
import  sqlite3
from    loguru      import logger


class TTableException(Exception):
    """ The table exception: trying to insert a double row-id """
    def __init__(self, message: str):
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
    """ Represents a sorting order enumeration providing different sorting
    states that can be used to specify how elements should be sorted in
    an application.

    This class can be used to define sorting order preferences for lists
    or collections, supporting ascending, descending, or no specific
    order. It provides a clear and concise way to handle sorting logic
    without using plain integers or strings, ensuring better
    readability and maintainability of the code.

    :ivar NONE: Represents no specific sorting order.
    :type NONE: int
    :ivar ASC: Represents ascending sorting order.
    :type ASC: int
    :ivar DESC: Represents descending sorting order.
    :type DESC: int
    """
    NONE    = 0  #: :meta private:
    ASC     = 1  #: :meta private:
    DESC    = 2  #: :meta private:


@dataclass(kw_only=True)
class TTableCell:
    """
    Represents a table cell with properties such as name, value, width, index,
    type, and additional user-defined attributes. This class is used to store
    and manage the properties of a single cell within a table structure. It
    provides default values for width, index, type, and allows the inclusion
    of custom attributes if necessary.

    :ivar name: Name of the column.
    :type name: str
    :ivar value: Value of the cell.
    :type value: Any
    :ivar width: Calculated width of a cell.
    :type width: int
    :ivar index: Calculated index of a cell.
    :type index: int
    :ivar type: Calculated type (could also be user defined).
    :type type: str
    :ivar attrs: User attributes.
    :type attrs: dict
    """
    name:       str                 #: :meta private: Name of the column
    value:      Any                 #: :meta private: Value of the cell
    width:      int     = 10        #: :meta private: calculated width of a cell
    index:      int     = 0         #: :meta private: calculated index of a cell
    type:       str     = 'str'     #: :meta private: calculated type (could also be user defined)
    attrs:      dict    = None      #: :meta private: user attributes


@dataclass(kw_only=True)
class TTableColumn:
    """
    Represents a column in a table with customizable properties.

    This class is designed to encapsulate the properties and behaviors of a
    table column, offering options to set and modify its header, attributes,
    index, width, alias, sorting preference, data type, and filtering
    criteria. It is typically used in table structures where columns may need
    specific customization for display or processing.

    :ivar header: Name of the column.
    :type header: str
    :ivar attrs: Customizable attributes of the column.
    :type attrs: dict
    :ivar index: Calculated index of the column.
    :type index: int
    :ivar width: Calculated width of the column.
    :type width: int
    :ivar alias: Alias name for output.
    :type alias: str
    :ivar sort: Sort direction of the column.
    :type sort: bool
    :ivar type: Type of the column.
    :type type: str
    :ivar filter: Filter string.
    :type filter: str
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
    """
    Represents a table row, capable of handling both simple and complex table structures.

    The TTableRow class is designed to facilitate the management and manipulation of table
    rows, allowing for both straightforward data representation and handling of more complex
    recursive data structures within a table. This class supports automatic conversion of
    string lists into cells and provides properties for interacting with row elements by column
    name or index.

    :ivar cells: A list of TTableCell objects or strings. Strings are automatically converted
                 into TTableCell objects during initialization.
    :ivar cells_filter: Filtered cells used for re-ordering and alias names, intended for
                        internal use only.
    :ivar column_descr: The column descriptor holds the attributes of the columns.
    :ivar index: Unique address for the columns, intended for internal use only.
    :ivar row_id: Unique row id of the row, valid for the entire table, intended for internal
                  use only.
    :ivar child: A row could handle recursive data structures, intended for internal use only.
    :ivar type: Customizable type used for triggering template output, intended for internal
                use only.
    :ivar attrs: Customizable row attributes, intended for internal use only.
    :type cells: List[TTableCell] | List[str]
    :type cells_filter: List[TTableCell]
    :type column_descr: List[str]
    :type index: int
    :type row_id: str
    :type child: TTable
    :type type: str
    :type attrs: dict
    """
    cells: List[TTableCell] | List[str]  #: :meta private: A list of strings are converted to a list of TTableCells
    cells_filter: List[TTableCell] = None  #: :meta private: Filtered cells used for re-ordering and alias names.
    column_descr: List[str] = None      #: :meta private: The column descriptor holds the attributes of the columns
    index:      int         = None      #: :meta private: Unique address for the columns
    row_id:     str         = None      #: :meta private: Unique row id of the row, valid for the entire table
    child:      TTable      = None      #: :meta private: A row could handle recursive data structures
    type:       str         = 'body'    #: :meta private: Customizable type used for triggering template output
    attrs:      dict        = None      #: :meta private: Customizable row attributes

    @property
    def id(self):
        """ Computes the SHA1 hash of the `row_id` attribute encoded in UTF-8.

        This property provides a unique string identifier for an object by
        hashing its `row_id` attribute. This can be particularly useful for
        ensuring consistent, non-collision identifiers across distributed systems
        or unique object tracking.

        :return: The SHA1 hash of the `row_id` as a hexadecimal string.
        :rtype: str
        """
        return SHA1.new(self.row_id.encode('utf8')).hexdigest()

    def __post_init__(self):
        """ Create a row, converting the values to :py:obj:`eezz.table.TTableCell` """
        if type(self.cells) is List[str]:
            self.cells = [TTableCell(name=str(x), value=str(x)) for x in self.cells]

        self.column_descr = [x.name for x in self.cells]

        if self.attrs:
            for x, y in self.attrs.items():
                setattr(self, x, y)

    def get_values_list(self) -> list:
        """
        Retrieves a list of values from the cells.

        This method iterates over the cells and extracts their values into a list.

        :return: A list containing values of the cells.
        :rtype: list
        """
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
    column_names:       List[str]                       #: :meta private: List of column names
    title:              str         = 'TTable'          #: :meta private: Table title name
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
    navigation:         TNavigation = TNavigation
    id:                 str         = None

    def __post_init__(self):
        """ Post init for a data class
        The value for self.format_types could be customized for own data type formatting
        The formatter sends size aad value of the column and receives the formatted string """

        # Init the UserList and keep track on the table instances
        super().__init__()
        self.table_index   = dict()
        self.visible_items = int(self.visible_items)

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
        """
        Appends a new row to the table. The new row can include custom attributes,
        a specified type, and a unique identifier. If `row_id` already exists in
        the table, the function will handle it based on the `exists_ok` parameter.
        Appropriate cell types, widths, and descriptors are determined and updated
        accordingly. The added row is indexed and stored within the table structure.

        :param table_row:   List of values representing a single row in the table.
        :type table_row:    list
        :param attrs:       Optional dictionary of attributes for the table row.
        :type attrs:        dict, optional
        :param row_type:    Type of the row, default is 'body'.
        :type row_type:     str
        :param row_id:      Unique identifier for the row. If not provided, defaults
                            to the row index.
        :type row_id:       str
        :param exists_ok:   If True, allows appending of a row with an existing
                            row_id without raising an exception.
        :type exists_ok:    bool
        :return:            The newly created table row object.
        :rtype:             TTableRow
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
                raise TTableException(f'InsertException: row-id already exists {table_row}: {row_id}')
            return self.table_index.get(row_id)

        x_cells = [TTableCell(name=x_descr.header, width=len(str(x_cell)), value=x_cell, index=x_descr.index, type=x_descr.type) for x_cell, x_descr in x_row_descr]
        x_row   = TTableRow(index=x_inx, cells=x_cells, attrs=attrs, type=row_type, row_id=row_id, column_descr=self.column_names)

        super(UserList, self).append(x_row)
        self.table_index[row_id] = x_row

        for x_cell, x_descr in x_row_descr:
            x_descr.width = max(len(str(x_cell)), x_descr.width)
        return x_row

    def get_header_row(self) -> TTableRow:
        """ Retrieves the header row of a table, applying a filter to the columns if
        necessary. If the `apply_filter_column` attribute is set to True, selects
        the visible columns according to the order specified in `column_names_filter`
        and maps these columns to new names specified in `column_names_alias`.

        :return: The table header row, potentially filtered and with aliased column
                 names, encapsulated in a `TTableRow` object.
        :rtype: TTableRow
        """
        if self.apply_filter_column:
            # Select the visible columns in the desired order and map the new names
            self.header_row.cells_filter = [deepcopy(self.header_row.cells[x]) for x in self.column_names_filter]
            for x in self.header_row.cells_filter:
                x.value = self.column_names_alias[x.value]
        return self.header_row

    def get_selected_row(self):
        return self.selected_row

    def get_next_values(self, search_filter: Callable[[TTableRow], bool]) -> tuple:
        """ Iterates over rows in the dataset and yields a tuple of values for each
        row that matches the given search filter. The search filter is a callable
        that should return a boolean indicating whether a particular row matches
        the criteria.

        :param search_filter: A callable function that takes a TTableRow object
                              and returns a boolean indicating whether the row
                              matches the criteria.
        :return: Tuple of values from each matched row.
        """
        x_row: TTableRow
        for x_row in self.data:
            if search_filter(x_row):
                yield tuple(x_value for x_value in x_row.get_values_list())

    def on_select(self, index: str) -> TTableRow | None:
        """ Updates the selected row in the table if a row with the given index
        exists. If the row exists, it sets the selected row to it and
        returns the row. If the row does not exist, it returns None.

        :param index: The unique identifier for the table row that is
                      to be selected.
        :return: The selected table row if it exists, otherwise None.
        """
        if selected_row := self.table_index.get(index):
            self.selected_row = selected_row
            return self.selected_row
        else:
            return None

    def do_select(self, get_all: bool = False, filter_descr: list = None) -> list:
        """ Executes a SELECT statement on the SQLite database associated with the current
        object and retrieves data based on the specified filter and options. The data
        can be fetched from an existing database or an in-memory table. Supports optional
        sorting and offset logic.

        The method allows focusing on retrieving either all records or a subset
        defined by internal pagination settings. It uses SQLite3 with Python's datetime
        support for converting date and time objects seamlessly. The method supports
        custom filtering through the `filter_descr` argument, which works with
        predefined column descriptions to create conditional where clauses.

        :param get_all: A boolean flag indicating whether to retrieve all records
            from the database. If set to True, the method retrieves all available
            data. If set to False, the method retrieves a limited number of records
            based on the current pagination settings.
        :param filter_descr: A list of filters to be applied during the data selection.
            These filters guide the construction of the SQL WHERE clause and determine
            which records are included in the result set.
        :return: A list containing the fetched records from the database. The records
            fetched are determined by filter conditions or sorting and pagination
            settings depending on the provided arguments.
        """
        sqlite3.register_adapter(datetime, lambda x_val: x_val.isoformat())
        sqlite3.register_converter("datetime", lambda x_val: datetime.fromisoformat(x_val.decode()))
        self.is_synchron = True

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
            logger.debug(f'TTable.do_select: {x_create_stm}')
            x_cursor.execute(x_create_stm)

            x_insert_stm = f"""insert into {self.title} values ({('?,' * len(self.column_names))} ?)"""
            logger.debug(f'TTable.do_select: {x_insert_stm}')

            x_row: TTableRow
            for i, x_row in enumerate(self.data):
                x_cursor.execute(x_insert_stm, tuple(x_row.get_values_list() + [i]))
            x_database.commit()

        if filter_descr:
            x_where, x_args = self.create_filter(filter_descr)
            x_select_stm = f"""select * from {self.title} where {x_where} {x_sort_stm} {x_options}"""
            logger.debug(f'TTable.do_select: {x_select_stm}')
            x_cursor.execute(x_select_stm, tuple(x_args))
        else:
            x_cursor.execute(f"""select * from {self.title} {x_options}""")
        yield from x_cursor.fetchall()

    def create_filter(self, filter_descr: List[List[str]]) -> tuple:
        """ Constructs a SQL filter query and its corresponding arguments
        from a structured filter description. The filter description
        consists of nested lists representing conditions connected by
        logical "and" and "or" operators. Each condition within "and" is
        specified as a string with a column name, an operator, and a value.

        :param filter_descr: A list of lists where each inner list
                             contains strings representing individual
                             conditions in the format
                             "<column_name> <operator> <value>".
        :type filter_descr: List[List[str]]
        :return: A tuple containing the constructed SQL filter query as
                 a string and a list of arguments corresponding to the
                 placeholders in the SQL query.
        :rtype: tuple
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
        """ Retrieves visible rows from the data source. The rows are filtered based
        on column descriptions and filter expressions, and can be further controlled
        by whether all rows should be retrieved or just a limited visible set.

        :param get_all: Determines whether to retrieve all rows without counting
            against the visible items limit. Defaults to False.
        :type get_all: bool
        :return: A generator yielding visible rows that match the filter criteria.
        :rtype: List[TTableRow]
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
        """
        Adjusts the current navigation offset based on the specified navigation
        command and position. It calculates a new offset value for navigating
        within a data structure while ensuring that boundaries are respected.
        The offset determines the starting point for visible items and can be
        adjusted using different navigation strategies such as moving to the
        next, previous, absolute position, top, or last items in the structure.

        :param where_togo: Determines the navigation strategy. The navigation
                           can be to the 'NEXT' item, 'PREV' item, an 'ABS'olute
                           position, 'TOP' of the data, or the 'LAST' item.
        :param position: Used when the 'ABS' navigation strategy is selected.
                         Determines the target position for the offset in the
                         data structure.
        :return: None
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

    def do_sort(self, column: int | str, reverse: bool = False) -> TTable:
        """ Sorts the table by a specified column. This method allows sorting in
        ascending or descending order based on the `reverse` flag. The column to
        be sorted can be specified using either its index or name.

        :param column: The column by which the table should be sorted. It can be
                       specified as an integer (index) or a string (name).
        :type column: int | str
        :param reverse: Determines the order of sorting. If True, the table is
                        sorted in descending order; otherwise, it is sorted in
                        ascending order. Default is False.
        :type reverse: bool
        :return: The sorted table object.
        :rtype: TTable
        """
        super().sort(key=lambda x_row: x_row[column], reverse=reverse)
        return self

    def print(self, level: int = 0, file=sys.stdout) -> None:
        """ Prints the table with the specified formatting and indentation level. The table
        headers are determined based on the column descriptions, and the rows are
        printed with respect to the visibility and formatting criteria applied. Each row
        can have a hierarchy with child rows being printed recursively at increasing
        indentation levels.

        :param level:
            The indentation level to be applied to the printed table. Default is 0.
            This affects the amount of whitespace before the table data, enhancing
            readability for nested (child) tables.
        :param file:
            An optional output stream to which the table will be printed. Default is
            `sys.stdout`, which represents standard output.
        :return:
            This function does not return any value. It directly prints the formatted
            table to the specified output.
        """
        x_offset        = ' ' * 6 * level
        x_column_descr  = [self.column_descr[x] for x in self.column_names_filter] if self.apply_filter_column \
                      else self.column_descr

        print(f'{x_offset}Table: {self.title}', file=file)
        x_formatted_row = '|'.join([' {{:<{}}} '.format(x_col.width).format(x_col.alias)  for x_col in x_column_descr])  if self.apply_filter_column \
                    else ('|'.join([' {{:<{}}} '.format(x_col.width).format(x_col.header) for x_col in x_column_descr]))
        print(f'{x_offset}|{x_formatted_row}|', file=file)

        for x_row in self.get_visible_rows():
            x_cells         = [x_row.cells[x] for x in self.column_names_filter] if self.apply_filter_column else x_row.cells
            x_row_descr     = zip(x_cells, x_column_descr)
            x_format_descr  = [(x_descr.type, x_descr.width,     x_cell.value) if x_descr.type in self.format_types
                          else ('str',        x_descr.width, str(x_cell.value)) for x_cell, x_descr in x_row_descr]

            x_formatted_row = '|'.join([self.format_types[x_type](x_width, x_value) for x_type, x_width, x_value in x_format_descr])
            print(f'{x_offset}|{x_formatted_row}|', file=file)
            if x_row.child:
                x_row.child.print(level + 1)


def test_table():
    """:meta private:"""
    logger.debug("Create a table and read the directory with attribute: [File, Size, Access] and print")
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
        except TTableException as x_except:
            logger.debug('Check row-id: Add entries with same row-id should be rejected')
            logger.debug(f'TableInsertException {x_item.name}: {x_except}')
            break

    logger.debug(f'table header = {[x.value for x in x_table.get_header_row().cells]}')
    debug_out = io.StringIO()
    x_table.print(file=debug_out)
    logger.debug(debug_out.getvalue())

    logger.debug("--- Output restricted on File and Size, change the position and translate the column names")
    x_table.filter_columns({'Size': 'Größe', 'File': 'Datei'})
    debug_out = io.StringIO()
    x_table.print(file=debug_out)
    logger.debug(debug_out.getvalue())

    logger.debug('--- Sort for column Size')
    x_table.apply_filter_column = False
    x_table.do_sort('Size')
    x_table.print()

    logger.debug('--- Restrict number of visible items')
    x_table.visible_items = 5
    debug_out = io.StringIO()
    x_table.print(file=debug_out)
    logger.debug(debug_out.getvalue())

    logger.debug('--- Navigate to next')
    x_table.navigate(where_togo=TNavigation.NEXT)
    debug_out = io.StringIO()
    x_table.print(file=debug_out)
    logger.debug(debug_out.getvalue())

    # x_result = [x for x in x_table.do_select(get_all=True, filter_descr=[['Size > 20000'],['File like %py']])]
    x_result = [x for x in x_table.do_select(get_all=True, filter_descr=[["Size > 10000"],["File like %.py"]])]
    logger.debug(f'--- result = {x_result}')

    x_table.visible_items = 100
    x_table.filter_rows([["Size > 10000" ],["File like %.py"]])
    x_table.print()


if __name__ == '__main__':
    """:meta private:"""
    TService.set_environment(root_path=r'C:\Users\alzer\Projects\github\eezz_full\webroot')

    # x_log_path = TService().logging_path / 'table.log'
    test_table()
