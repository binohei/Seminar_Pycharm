import sys
from time import time
HEADER_SIZE = 12

class RtpPacket:
    def __init__(self):
        # header và payload là thuộc tính instance (không dùng biến class)
        self.header = bytearray(HEADER_SIZE) # khởi tạo header (12 bytes)
        self.payload = b''  # payload (rỗng)

    def encode(self, version, padding, extension, cc, seqnum, marker, pt, ssrc, payload):
        """Encode the RTP packet with header fields and payload."""
        timestamp = int(time()) # Lấy thời gian hiện tại làm timestamp
        header = bytearray(HEADER_SIZE) # Tạo header mới

        # Byte 0: V(2), P(1), X(1), CC(4)
        # Byte 0: Ghép 4 trường thành 1 byte
        # version << 6: dịch version 6 bit sang trái (2 bit đầu)
        # padding << 5: dịch padding 5 bit sang trái (bit thứ 3)
        # extension << 4: dịch extension 4 bit sang trái (bit thứ 4)
        # cc & 0x0F: lấy 4 bit cuối của cc
        header[0] = (version << 6) | (padding << 5) | (extension << 4) | (cc & 0x0F)

        # Byte 1: M(1), PT(7)
        # Byte 1: Marker bit (1 bit) và Payload Type (7 bit)
        header[1] = ((marker & 0x01) << 7) | (pt & 0x7F)

        # Seqnum (16 bit) - big
        # Sequence Number (16 bit) - chia thành 2 bytes
        header[2] = (seqnum >> 8) & 0xFF # Byte cao nhất
        header[3] = seqnum & 0xFF  # Byte thấp nhất

        # Timestamp (32 bit)
        # Timestamp (32 bit) - chia thành 4 bytes
        header[4] = (timestamp >> 24) & 0xFF # Byte cao nhất
        header[5] = (timestamp >> 16) & 0xFF
        header[6] = (timestamp >> 8) & 0xFF
        header[7] = timestamp & 0xFF  # Byte thấp nhất

        # SSRC (32 bit)
        # SSRC - Synchronization Source (32 bit)
        header[8]  = (ssrc >> 24) & 0xFF
        header[9]  = (ssrc >> 16) & 0xFF
        header[10] = (ssrc >> 8) & 0xFF
        header[11] = ssrc & 0xFF

        self.header = header
        # ensure payload is bytes type
        # Đảm bảo payload là kiểu bytes
        self.payload = payload if isinstance(payload, (bytes, bytearray)) else bytes(payload)

    def decode(self, byteStream):
        """Decode the RTP packet. byteStream: bytes or bytearray."""
        bs = bytearray(byteStream)
        self.header = bs[:HEADER_SIZE]
        self.payload = bytes(bs[HEADER_SIZE:])

    def version(self):
        """Return RTP version."""
        return (self.header[0] >> 6) & 0x03

    def seqNum(self):
        """Return sequence (frame) number."""
        return (self.header[2] << 8) | self.header[3]

    def timestamp(self):
        """Return timestamp."""
        return (self.header[4] << 24) | (self.header[5] << 16) | (self.header[6] << 8) | self.header[7]

    def payloadType(self):
        """Return payload type."""
        return self.header[1] & 0x7F

    def getPayload(self):
        """Return payload (bytes)."""
        return bytes(self.payload)

    def getPacket(self):
        """Return RTP packet as bytes (header + payload)."""
        return bytes(self.header) + bytes(self.payload)

    def marker(self):
        """Return the Marker bit (M bit) as 0 or 1."""
        return (self.header[1] >> 7) & 0x01

    # CLIENT-SIDE CACHING - CREATE HASH FOR FRAME
    def getFrameHash(self):
        """Tạo hash duy nhất cho frame để sử dụng trong caching system"""
        import hashlib
        return hashlib.md5(self.payload).hexdigest()[:16] # Lấy 16 ký tự đầu