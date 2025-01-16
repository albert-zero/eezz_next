import base64
import os
from   pathlib          import Path
from   datetime         import datetime, timezone
from   eezz.table       import TTable, TTableRow
from   eezz.tabletree   import TTableTree
from   loguru           import logger
import time


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


class TDirAsync(TTable):
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

    def push_time(self) -> bytes:
        time.sleep(3.0)
        now = datetime.now()
        return str(now.time()).encode('utf8')


class TDirTree(TTableTree):
    def __init__(self, title: str, path: str, visible_items=20):
        # noinspection PyArgumentList
        super().__init__(column_names=['File', 'Size', 'Access Time'], title=title, path=path, visible_items=visible_items)
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


class TDirTreeDetails(TTableTree):
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


# from icecream import ic
def test():
    x_row = TDirView(title='directory-view', path = r'\Users\alzer\Projects')
    x_row.print()

    my_table = TDirTree(title='tree-view', path = r'\Users\alzer\Projects')
    my_table.print()

    x_row = my_table[0]
    logger.debug(f'row = {x_row[0]}, type = {x_row.type}')
    x_sub_table = my_table.open_dir(x_row.row_id)
    x_sub_table.child.print()
    logger.debug(x_sub_table)

    my_table = TDirTree(title='tree-view', path = r'\Users\alzer\Projects\nofile')
    my_table.print()

    my_table = TDirTreeDetails(title="details", path=r"\Users\alzer\Projects\github\eezz_full\webroot\applications\examples")
    x_buffer = my_table.read_file(r'\Users\alzer\Projects\github\eezz_full\webroot\applications\examples\directory.py')
    print(base64.b64encode(x_buffer)[:20])
    my_table.print()


if __name__ == '__main__':
    test()
