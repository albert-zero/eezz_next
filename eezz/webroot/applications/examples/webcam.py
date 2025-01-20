import  time
import  cv2
from    io      import BytesIO
from    eezz.table   import TTable
from    loguru  import logger


class TCamera(TTable):
    def __init__(self):
        self.cam = cv2.VideoCapture(0)
        super().__init__(column_names=['camera'])
        logger.debug(self.column_names)

    def read_frame(self) -> bytes:
        time.sleep(5)
        ret, frame  = self.cam.read()
        ret, buffer = cv2.imencode('.png', frame)
        jpg = BytesIO(buffer)
        return jpg.getvalue()


if __name__ == '__main__':
    pass
