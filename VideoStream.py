class VideoStream:
    def __init__(self, filename):
        self.filename = filename
        try:
            self.file = open(filename, 'rb')
        except:
            raise IOError
        self.frameNum = 0

    def nextFrame(self):
        """Get next frame."""
        data = self.file.read(
            5)  # Get the framelength from the first 5 bytes, [5 bytes framelength][frame data][5 bytes framelength][frame data]...
        if data:
            framelength = int(data)  # lấy ra chiều dài của khung bằng số nguyên

            # Read the current frame
            data = self.file.read(framelength)  # đọc cái khung hiện tại
            self.frameNum += 1  # tăng số lượng khung lên
        return data

    def frameNbr(self):
        """Get frame number."""
        return self.frameNum  # trả về số thứ tự của khung
