"""
    This module implements the following classes:

    * :py:class:`eezz.table.TTableTree`:\
    Implements a Tree representation, where each node is a TTable with TTableRow entries. \
    Nodes could be expanded and collapsed.


"""
from table      import TTable, TTableRow
from typing     import override, List
from abc        import abstractmethod
from pathlib    import Path
from loguru     import logger


class TTableTree(TTable):
    """
    Represents a tree structure of tables where each node is a table and can contain other tables as children.

    :ivar nodes:        List of table nodes within the tree.
    :type nodes:        List[TTable]
    :ivar root_path:    Path of the table node in the directory structure.
    :type nodes:        Path
    """
    def __init__(self, column_names: list[str], title: str, path: str) -> None:
        super().__init__(column_names=column_names, title=title)
        self.root_path            = Path(path)
        self.nodes: List[TTable]  = [self]
        self.expanded: bool       = False
        self.selected: TTableTree = None

    @override
    def append(self, table_row: list, attrs: dict = None, row_type: str = 'body', row_id: str = '', exists_ok=False) -> TTableRow:
        """
        Append a new row to the table with optional attributes and a specific row type.

        This method takes a list of table row values and the optionally attributes row-type.
        The incoming row-ID must be set to a file entry in specified table path.
        A new unique row-ID is calculated combining TTableTree.root_path and row-ID, which allows to
        address the row in the entire tree as "index" parameter

        Then, it appends the row to the table.

        :param table_row:   A list representing the contents of the table row.
        :param row_id:      A string representing a file in a directory.
        :param attrs:       A dictionary of attributes to set for the table row (default is None).
        :param row_type:    A string representing the type of row, e.g., 'is_file', 'is_dir' (default is 'body').
        :param exists_ok:   If True, supress exception, trying to insert the same row-ID
        :return: An instance of TTableRow representing the appended row.
        """
        if not row_id:
            row_id = '/'.join([str(x) for x in table_row if isinstance(x, str)])
        x_path = self.root_path / row_id
        x_hash = x_path.as_posix() # SHA1.new(x_path.encode('utf8')).hexdigest()
        return super().append(table_row, row_id=x_hash, row_type=row_type)

    @abstractmethod
    def read_dir(self):
        """ Implement this method to fill the table using self.root_path
        """
        pass

    @override
    def on_select(self, index: str) -> TTableRow:
        """
        Handles the selection of a table row by a given index within a tree.

        This method iterates through the nodes and checks if a row is selected by
        calling the parent class's on_select method. If a row is found, it returns
        the selected table row.

        :param index:   The index of the table row to select.
        :type index:    str
        :return:        The selected table row if found.
        :rtype:         TTableRow
        """
        for x_table in self.nodes:
            if x_row := super(TTableTree, x_table).on_select(index):
                return x_row

    def open_dir(self, path: str) -> TTableRow | None:
        """
        This class provides functionalities to expand or collapse node elements in a tree
        based on their index. Expansion status is toggled, i.e., if an element
        is currently collapsed, it will be expanded and vice versa.

        If the subtree is expensive to calculate, override this method and omit the
        clearing of data .
        """
        for x_table in self.nodes:
            if x_row := super(TTableTree, x_table).on_select(path):
                if x_row.child:
                    if not x_row.child.expanded:
                        x_row.child.expanded = True
                        return x_row
                    x_row.child.expanded = False

                    # Possible return None without clearing possible
                    self.nodes.remove(x_row.child)
                    x_row.child.clear()
                    x_row.child = None
                else:
                    x_row.child = self.__class__(title=self.title, path=x_row.row_id)
                    x_row.child.expanded = True
                    x_row.child.read_dir()
                    self.nodes.append(x_row.child)
                self.selected = x_row
                return x_row
        return None

    def read_file(self, path: str) -> bytes:
        """
        Reads the contents of a file specified by the given path and returns the
        data in binary format. If the file does not exist or cannot be read,
        the function returns an empty binary string.

        :param path:    The file system path to the file that needs to be read.
                        It should be a string representing the path to the file.
        :return:        A binary string containing the contents of the file if it exists,
                        otherwise an empty binary string.
        """
        x_full_path = Path(path)
        if x_full_path.is_file():
            with x_full_path.open('rb') as f:
                x_buffer = f.read()
            return x_buffer
        return b''


import os
from   datetime import datetime, timezone


class TTestTree(TTableTree):
    """ :meta private: """
    def __init__(self, title: str, path: str):
        # noinspection PyArgumentList
        self.path        = Path(path)
        self.table_title = f'{title}/{self.path.stem}'
        super().__init__(column_names=['File', 'Size', 'Access Time'], title=self.table_title, path=path)
        self.read_dir()

    def read_dir(self) -> TTable:
        """ :meta private: """
        self.data.clear()
        for x in self.path.iterdir():
            x_stat = os.stat(x)
            x_time = datetime.fromtimestamp(x_stat.st_atime, tz=timezone.utc)
            self.append([str(x.name), x_stat.st_size, x_time], row_type='is_dir' if x.is_dir() else 'is_file')
        return self


def test_table_tree():
    """ :meta private: """
    x_tree = TTestTree('TestTree:', Path.cwd().as_posix())

    for x in x_tree.get_visible_rows():
        if x.type == 'is_dir':
            x_tbl = x_tree.open_dir(x.row_id)
    x_tree.print()


if __name__ == '__main__':
    """ :meta private: """
    test_table_tree()
