from table import TTable


class TCalc(TTable):
    def __init__(self):
        self.number_input: int  = 0
        self.stack: list        = list()
        self.op:    str         = ''

        super().__init__(column_names=['display', 'numpad', 'op'], visible_items=1)
        self.append(['0', [1,2,3,4,5,6,7,8,9,0], ['+','-','*','=']])

    def key_pad_input(self, key) -> int:
        self.number_input *= 10
        self.number_input += key
        return self.number_input

    def key_op_input(self, key) -> float:
        result: float = 1.0 * self.number_input

        self.stack.append(self.number_input)
        self.number_input = 0

        if len(self.stack) == 1:
            self.stack.append(key)
        if len(self.stack) == 3:
            if self.stack[1] == '+':
                result = self.stack[0] + self.stack[2]
            elif self.stack[1] == '-':
                result = self.stack[0] - self.stack[2]
            elif self.stack[1] == '*':
                result = self.stack[0] * self.stack[2]
            self.stack.clear()

            if key != '=':
                self.stack.append(result)
        return result


if __name__ == '__main__':
    calc = TCalc()
    calc.key_pad_input(1)
    calc.key_pad_input(1)
    print(calc.key_op_input('*'))
    calc.key_pad_input(2)
    calc.key_pad_input(2)
    print(calc.key_op_input('='))

