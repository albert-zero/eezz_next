"""
    TTableTree implements a Tree representation, where each node is a TTable with
    TTableRow entries

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
        self.root_path           = Path(path)
        self.nodes: List[TTable] = [self]
        self.expanded: bool      = False

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
        Handles the selection of a table row by a given index.

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

    def exco(self, index: str) -> TTable | None:
        """
        This class provides functionalities to expand or collapse node elements in a tree
        based on their index. Expansion status is toggled, i.e., if an element
        is currently collapsed, it will be expanded and vice versa.

        If the subtree is expensive to calculate, override this method and omit the
        clearing of data .
        """
        for x_table in self.nodes:
            if x_row := super(TTableTree, x_table).on_select(index):
                if x_row.child:
                    if not x_row.child.expanded:
                        x_row.child.expanded = True
                        return x_row.child
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
                return x_row.child
        return None

    def expand(self, index: str) -> TTable | None:
        """
        Expands the current table node based on a specified index.

        This method iterates through the nodes of the current table, selects
        the row corresponding to the given index, and performs the following
        actions:
        - If the row has a child, it returns that child.
        - If the row does not have a child, the method creates a new child
          instance, reads the directory for the child, appends it to the nodes of
          the current table, and returns the newly created child.

        The child node inherits the layout from the parent node. It would be possible
        to override this and create nodes with different layouts.

        :param index:   The index used to identify and expand the corresponding row in the table.
        :type index:    str
        :return:        The expanded child table node if found, otherwise None.
        :rtype:         TTable or None, if the index is not found
        """
        for x_table in self.nodes:
            if x_row := x_table.on_select(index):
                if x_row.child:
                    return x_row.child
                x_path      = x_row.row_id
                x_row.child = self.__class__(title=self.title, path=x_path)
                x_row.child.read_dir()
                x_row.child.expanded = True
                self.nodes.append(x_row.child)
                return x_row.child
        return None

    def collapse(self, index: str) -> None:
        """
        Collapses a node specified by the index in the tree.

        This method traverses through the nodes and collapses
        the child node of the selected row if it exists. The
        selected row is identified using the index parameter.

        :param index: The identifier of the node to collapse
        :type  index: str
        """
        for x_table in self.nodes:
            if x_row := x_table.on_select(index):
                if x_row.child:
                    self.nodes.remove(x_row.child)
                    x_row.child.clear()
                    x_row.child = None
                return


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
            x_tbl = x_tree.exco(x.row_id)
    x_tree.print()


if __name__ == '__main__':
    """ :meta private: """
    test_table_tree()
