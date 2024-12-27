import  time
import  cv2
import  numpy   as np
from    io      import BytesIO
from    table   import TTable
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



def test_camera():
    cam = cv2.VideoCapture(0)
    frame_width = int(cam.get(cv2.CAP_PROP_FRAME_WIDTH))
    frame_height = int(cam.get(cv2.CAP_PROP_FRAME_HEIGHT))
    # Define the codec and create VideoWriter object
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')

    # out = cv2.VideoWriter('output.mp4', fourcc, 20.0, (frame_width, frame_height))
    ret, frame = cam.read()
    cv2.imshow('Camera', frame)
    cv2.waitKey(0)
    cv2.destroyAllWindows()

def test2():
    # Capture a frame from a video or webcam
    cap = cv2.VideoCapture(0)
    ret, frame = cap.read()

    # Encode the frame as a JPEG
    ret, buffer = cv2.imencode('.jpg', frame)

    # Convert the buffer to a BytesIO object
    jpg_as_text = BytesIO(buffer)

    # Now you can use jpg_as_text as an in-memory JPEG file
    # For example, you can read it back into an image
    jpg_as_np = np.frombuffer(jpg_as_text.getvalue(), dtype=np.uint8)
    image = cv2.imdecode(jpg_as_np, cv2.IMREAD_COLOR)

    # Display the image
    cv2.imshow('Frame', image)
    cv2.waitKey(0)
    cv2.destroyAllWindows()

    # Release the capture
    cap.release()


if __name__ == '__main__':
    camera = TCamera()
    buffer = camera.read_frame()

    jpg_as_np = np.frombuffer(buffer, dtype=np.uint8)
