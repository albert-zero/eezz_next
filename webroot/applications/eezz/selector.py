from table import TTable
from loguru import logger
from dataclasses import dataclass


class TSelect(TTable):
    def __init__(self):
        super().__init__(column_names=['select'], title='footselect')
        self.append([['apple', 'banana', 'oranges', 'peach']])
        logger.info('call TSelect')


    def do_select_food(self, index: str):
        print(index)


@dataclass
class TErrorLogger(TTable):
    column_names: str = None

    def __post_init__(self):
        self.column_names = ['date', 'level', 'function', 'message']
        super().__post_init__()

    def add_message(self, msg):
        prepare = msg.split('|')
        result = [x.strip() for x in prepare[:-1]] + [x.strip() for x in prepare[-1].split('-')]
        #  print(f'catch {result}')
        self.append(result)


def log_handler(msg):
    prepare = msg.split('|')
    output  = prepare[-1].split('-')
    print(f'catch {[x.strip() for x in prepare]} {[x.strip() for x in output]} {output[0].split(':')}')


if __name__ == '__main__':
    xerr = TErrorLogger()
    logger.add(xerr.add_message)
    logger.debug('hello')
    xsel = TSelect()

    logger.success('finish')
    xerr.print()