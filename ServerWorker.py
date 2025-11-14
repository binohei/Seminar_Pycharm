from random import randint
import sys, traceback, threading, socket

from VideoStream import VideoStream
from RtpPacket import RtpPacket

class ServerWorker:
    SETUP = 'SETUP'
    PLAY = 'PLAY'
    PAUSE = 'PAUSE'
    TEARDOWN = 'TEARDOWN'

    INIT = 0
    READY = 1
    PLAYING = 2

    OK_200 = 0
    FILE_NOT_FOUND_404 = 1
    CON_ERR_500 = 2

    def __init__(self, clientInfo):
        # clientInfo expected: {'rtspSocket': (connSocket, (addr,port)), ...}
        self.clientInfo = clientInfo
        self.state = self.INIT

    def run(self): # chạy hàm này
        threading.Thread(target=self.recvRtspRequest, daemon=True).start() # bắt đầu xử lý trong luồng

    def recvRtspRequest(self):
        """Receive RTSP request from the client (blocking)."""
        try:
            connSocket = self.clientInfo['rtspSocket'][0] # lấy socket mới tạo ra để nhận và gửi dữ liệu
        except KeyError:
            print("No rtsp socket in clientInfo")
            return

        while True:
            try:
                data = connSocket.recv(4096) # nhận dữ liệu từ client
            except Exception:
                traceback.print_exc()
                break

            if not data:
                # connection closed by client
                break

            try:
                text = data.decode('utf-8', errors='ignore') # phân tích dữ liệu
                print("Data received:\n" + text)  # in ra cái gì đã nhận từ client
                self.processRtspRequest(text) # đưa vào trong quy trình xử lý
            except Exception:
                traceback.print_exc()

    def processRtspRequest(self, data):
        """Process RTSP request sent from the client."""
        # normalize lines (handle CRLF)
        request_lines = [line.strip() for line in data.splitlines() if line.strip() != ''] # lấy dữ liệu từ cái data
        if not request_lines:
            return

        # parse request line safely
        first_line_parts = request_lines[0].split() # split() để tách khoảng trắng
        if len(first_line_parts) < 2:
            return
        requestType = first_line_parts[0].upper() # chuyển thành chữ in hoa, để cho thấy cái yêu cầu là gì
        filename = first_line_parts[1] # tên file video muốn truyền vào

        # find CSeq
        seq = None
        for line in request_lines[1:6]:
            if line.upper().startswith('CSEQ'):
                parts = line.split(':', 1)
                if len(parts) == 2:
                    seq = parts[1].strip() # lấy ra cái cổng số mấy để theo dõi cái yêu cầu có được thực hiện hay không
                break

        # find Transport line (to get client_port)
        transport_line = None
        for line in request_lines[1:]:
            if line.upper().startswith('TRANSPORT'):
                transport_line = line  # lấy cái dòng cuối cùng
                break

        # HANDLE REQUEST TYPES
        if requestType == self.SETUP:
            if self.state == self.INIT:
                print("processing SETUP") # in ra dòng để bảo đang setup
                try:
                    self.clientInfo['videoStream'] = VideoStream(filename) # lấy cái video
                    self.state = self.READY # --> hiển thị ra để sẳn sàn cho việc chiếu
                except IOError:
                    # file not found -> reply 404
                    self.replyRtsp(self.FILE_NOT_FOUND_404, seq or '0')
                    return

                # generate session id
                self.clientInfo['session'] = randint(100000, 999999) # tạo một phiên làm việc giữa client và server để đảm bảo việc làm việc giữa hai đó không bị gián đoạn

                # parse client port from Transport header if present
                if transport_line and '=' in transport_line:
                    try:
                        port_str = transport_line.split('=')[-1].strip().strip('; ')
                        self.clientInfo['rtpPort'] = int(port_str) # lưu thông tin cổng rtp cho trường khách hàng
                    except Exception:
                        # fallback: leave rtpPort absent -> server will log error when sending
                        traceback.print_exc()

                # send 200 OK
                self.replyRtsp(self.OK_200, seq or '0')

        elif requestType == self.PLAY:
            if self.state == self.READY:
                print("processing PLAY")
                self.state = self.PLAYING

                # create UDP socket (for sending RTP)
                try:
                    self.clientInfo["rtpSocket"] = socket.socket(socket.AF_INET, socket.SOCK_DGRAM) # giao thức udp để gửi đoạn video
                except Exception:
                    traceback.print_exc()
                    self.replyRtsp(self.CON_ERR_500, seq or '0')
                    return

                # event to control send loop
                self.clientInfo['event'] = threading.Event() # tạo một cái điều khiển luồng

                # reply OK then start sender thread
                self.replyRtsp(self.OK_200, seq or '0') # gửi phản hồi
                worker = threading.Thread(target=self.sendRtp, daemon=True) # chỉ truyền tên hàm, tham chiếu tới hàm thôi chứ chưa chạy, còn nếu gọi sendRtp() thì nó sẽ trả về None

                self.clientInfo['worker'] = worker  # lưu thông tin luồng vào clientInfo
                worker.start() # bắt đầu luồng bằng việc gửi Rtp và daemon để luồng không cản trở chương trình chính kết thúc

        elif requestType == self.PAUSE:
            if self.state == self.PLAYING:
                print("processing PAUSE")
                self.state = self.READY # gán lại cái
                if 'event' in self.clientInfo:
                    self.clientInfo['event'].set() # KIỂM TRA CÁI NÀY CÓ ĐÚNG HAY KHÔNG
                self.replyRtsp(self.OK_200, seq or '0') # gửi phản hồi khách hàng

        elif requestType == self.TEARDOWN:
            print("processing TEARDOWN")
            if 'event' in self.clientInfo:
                self.clientInfo['event'].set() # gửi tiếp, khi mà bị dừng thì nó vẫn gửi video lên chứ không phải là dừng luôn
            self.replyRtsp(self.OK_200, seq or '0')
            # close RTP socket if exists
            if 'rtpSocket' in self.clientInfo: # kiểm tra sự tồn tại của RTP sockets để đóng nó lại
                try:
                    self.clientInfo['rtpSocket'].close()
                except Exception:
                    pass

    def sendRtp(self):
        """Send RTP packets over UDP (fragmenting large frames)."""
        MAX_RTP_PAYLOAD = 1400 # gửi tối đa bao nhiêu bytes
        event = self.clientInfo.get('event')
        video = self.clientInfo.get('videoStream')
        rtp_socket = self.clientInfo.get('rtpSocket')
        if not (event and video and rtp_socket): # nếu không có thông tin của những cái này thì nó sẽ bị dừng lại
            print("sendRtp: missing event/video/rtp_socket")
            return

        while True:
            # wait short time; if event set -> stop
            was_set = event.wait(0.05)  # chờ một xíu
            if was_set or event.is_set():
                break

            try:
                data = video.nextFrame()  # đọc cái khung tiếp theo
            except Exception:
                traceback.print_exc()
                break

            if not data:
                continue

            frameNumber = video.frameNbr() # lấy ra cái số thức tự của khung
            frame_size = len(data) # chiều dài của khung theo số nguyên
            num_chunks = (frame_size + MAX_RTP_PAYLOAD - 1) // MAX_RTP_PAYLOAD # chia khung đó ra thành nhiều khung để truyền gói đó đi

            # get client address (from RTSP socket info)
            try:
                rtsp_info = self.clientInfo.get('rtspSocket')
                if not rtsp_info or len(rtsp_info) < 2:
                    print("sendRtp: no rtspSocket address info")
                    break
                address = rtsp_info[1][0]
                port = int(self.clientInfo.get('rtpPort', 0))
                if port == 0:
                    print("sendRtp: rtpPort missing or zero")
                    break
            except Exception:
                traceback.print_exc()
                break

            for i in range(num_chunks):
                start = i * MAX_RTP_PAYLOAD
                end = min((i + 1) * MAX_RTP_PAYLOAD, frame_size)
                payload_chunk = data[start:end]
                marker_bit = 1 if (i == num_chunks - 1) else 0

                try:
                    rtp_packet = self.makeRtp(payload_chunk, frameNumber, marker_bit)
                    # ensure bytes
                    if isinstance(rtp_packet, str):
                        rtp_packet = rtp_packet.encode('latin1')
                    rtp_socket.sendto(rtp_packet, (address, port))
                except Exception:
                    print("Connection Error sending RTP chunk")
                    traceback.print_exc()
                    # break out of chunk loop on send error to avoid busy-looping
                    break

    def makeRtp(self, payload, frameNbr, marker=0):
        """RTP-packetize the video data."""
        version = 2
        padding = 0
        extension = 0
        cc = 0
        pt = 26  # MJPEG
        seqnum = frameNbr
        ssrc = 0

        rtpPacket = RtpPacket()
        rtpPacket.encode(version, padding, extension, cc, seqnum, marker, pt, ssrc, payload)
        return rtpPacket.getPacket()

    def replyRtsp(self, code, seq):
        """Send RTSP reply to the client."""
        if code == self.OK_200:
            # print("200 OK")
            reply = 'RTSP/1.0 200 OK\nCSeq: ' + seq + '\nSession: ' + str(self.clientInfo['session'])
            connSocket = self.clientInfo['rtspSocket'][0]
            connSocket.send(reply.encode())

        # Error messages
        elif code == self.FILE_NOT_FOUND_404:
            print("404 NOT FOUND")
        elif code == self.CON_ERR_500:
            print("500 CONNECTION ERROR")
