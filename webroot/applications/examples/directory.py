import sys
import os
from   pathlib     import Path
from   datetime    import datetime, timezone
from   table       import TTable, TTableRow
from   tabletree   import TTableTree
from   Crypto.Hash import SHA
from   loguru      import logger
from   typing      import List, override


class TDirView(TTable):
    """ Example class printing directory content """
    def __init__(self, title: str, path: str):
        # noinspection PyArgumentList
        super().__init__(column_names=['File', 'Size', 'Access Time'], title=title)
        self.path        = Path(path)
        self.table_title = 'Directory'
        self.read_dir()

    def read_dir(self) -> TTable:
        self.data.clear()
        for x in self.path.iterdir():
            x_stat = os.stat(x)
            x_time = datetime.fromtimestamp(x_stat.st_atime, tz=timezone.utc)
            self.append([str(x.name), x_stat.st_size, x_time], row_id=x.as_posix())
        return self


class TDirTree(TTableTree):
    def __init__(self, title: str, path: str):
        # noinspection PyArgumentList
        super().__init__(column_names=['File', 'Size', 'Access Time'], title=title, path=path)
        self.path = Path(path)
        if not self.path.is_dir():
            logger.error(f'{self.path.as_posix()} not a directory')
            return
        self.table_title = 'Simple Tree'
        self.read_dir()

    def read_dir(self) -> TTable:
        self.data.clear()
        for x in self.path.iterdir():
            x_stat = os.stat(x)
            x_time = datetime.fromtimestamp(x_stat.st_atime, tz=timezone.utc)
            self.append([str(x.name), x_stat.st_size, x_time], row_type='is_dir' if x.is_dir() else 'is_file', row_id=x.as_posix())
        return self


class TDirPng(TTable):
    """ Example using row type """
    def __init__(self, path: str):
        # noinspection PyArgumentList
        super().__init__(column_names=['File', 'Size', 'Access Time'], title='DirPng')

        a_path = Path(path)
        self.table_title = 'Directory'
        self.table_attrs = {'path': a_path.as_posix()}

        for x in a_path.iterdir():
            x_stat = os.stat(x)
            x_time = datetime.fromtimestamp(x_stat.st_atime, tz=timezone.utc)
            self.append([str(x.name), x_stat.st_size, x_time], row_type='is_dir' if x.is_dir() else 'is_file')


#from icecream import ic
def test():
    x_row = TDirView(title='directory-view', path = r'\Users\alzer\Projects')
    x_row.print()

    my_table = TDirTree(title='tree-view', path = r'\Users\alzer\Projects')
    my_table.print()

    x_row = my_table[0]
    logger.debug(f'row = {x_row[0]}, type = {x_row.type}')
    x_sub_table = my_table.open_dir(x_row.row_id)
    x_sub_table.print()

    my_table = TDirTree(title='tree-view', path = r'\Users\alzer\Projects\nofile')
    my_table.print()


if __name__ == '__main__':
    test()
    #exit()
    #xdir = TDirPng(path=str(Path.cwd() / '../docs'))
    #xdir.print()
    #xdir = TDirPng(path='/home/paul')
    #xdir.print()
