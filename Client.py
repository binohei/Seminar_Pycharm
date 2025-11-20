import sys
from time import time
from tkinter import *
import tkinter.messagebox as tkMessageBox
from PIL import Image, ImageTk
import socket, threading, sys, traceback, os
from collections import deque
from RtpPacket import RtpPacket
import hashlib
import tempfile

CACHE_FILE_NAME = "cache-"
CACHE_FILE_EXT = ".jpg"

# comment de push len

class Client:
    INIT = 0
    READY = 1
    PLAYING = 2

    SETUP = 0
    PLAY = 1
    PAUSE = 2
    TEARDOWN = 3

    def __init__(self, master, serveraddr, serverport, rtpport, filename):
        self.master = master
        self.master.protocol("WM_DELETE_WINDOW", self.handler)
        self.createWidgets()

        # Connection parameters
        self.serverAddr = serveraddr
        self.serverPort = int(serverport)
        self.rtpPort = int(rtpport)
        self.fileName = filename

        # RTSP state
        self.state = self.INIT
        self.rtspSeq = 0
        self.sessionId = 0
        self.requestSent = -1
        self.teardownAcked = 0

        # Frame tracking
        self.frameNbr = 0
        self.rtpBuffer = b''
        self.prevSeqNum = 0
        self.currentFrameNum = 0  # Track current frame

        # CLIENT-SIDE CACHING SYSTEM
        self.setup_caching_system()

        # Thread control
        self.playEvent = threading.Event()
        self.playEvent.clear()
        self.rtspSocket = None
        self.rtpSocket = None

        self.connectToServer()

    def setup_caching_system(self):
        """Thiết lập hệ thống caching"""
        # Memory cache
        self.frame_cache = {}   # Dictionary: {hash: frame_data}
        self.cache_hits = 0     # Đếm cache hits
        self.cache_misses = 0   # Đếm cache misses

        # Buffer với caching - TĂNG KÍCH THƯỚC BUFFER
        self.frameBuffer = deque()  # Queue for frames
        self.bufferSize = 120       # Maximum number of frames in buffer

        # Control flags
        self.isReceivingFrames = False
        self.isPlaying = False
        self.frameReceiverThread = None
        self.playbackThread = None

        # Performance tracking
        self.performance_stats = {
            'frames_received': 0,
            'frames_from_cache': 0,
            'start_time': time(),
            'last_frame_time': 0
        }

        # Frame timing control
        self.frameInterval = 0.042  # ~24 fps
        self.lastDisplayTime = 0
        self.frameDropCount = 0

        print("Initialized client-side caching system")

    def createWidgets(self):
        """Build GUI"""
        # Control buttons
        self.setup = Button(self.master, width=20, padx=3, pady=3, text="Setup", command=self.setupMovie)
        self.setup.grid(row=1, column=0, padx=2, pady=2)

        self.start = Button(self.master, width=20, padx=3, pady=3, text="Play", command=self.playMovie)
        self.start.grid(row=1, column=1, padx=2, pady=2)

        self.pause = Button(self.master, width=20, padx=3, pady=3, text="Pause", command=self.pauseMovie)
        self.pause.grid(row=1, column=2, padx=2, pady=2)

        self.teardown = Button(self.master, width=20, padx=3, pady=3, text="Teardown", command=self.exitClient)
        self.teardown.grid(row=1, column=3, padx=2, pady=2)

        # HIỂN THỊ THÔNG TIN CACHING VÀ BUFFER
        self.bufferLabel = Label(self.master, text="Buffer: 0/0")
        self.bufferLabel.grid(row=2, column=0, columnspan=2, padx=5, pady=2)

        self.cacheLabel = Label(self.master, text="Cache: 0%")
        self.cacheLabel.grid(row=2, column=2, columnspan=2, padx=5, pady=2)

        # Status label
        self.statusLabel = Label(self.master, text="Status: Initialized")
        self.statusLabel.grid(row=3, column=0, columnspan=4, padx=5, pady=2)

        # Video display
        self.label = Label(self.master, height=19)
        self.label.grid(row=0, column=0, columnspan=4, sticky=W + E + N + S, padx=5, pady=5)

    # CACHING METHODS
    def get_cached_frame(self, frame_hash):
        """Lấy frame từ cache nếu tồn tại"""
        if frame_hash in self.frame_cache:
            self.cache_hits += 1
            return self.frame_cache[frame_hash]
        self.cache_misses += 1
        return None

    def cache_frame(self, frame_hash, frame_data):
        """Lưu frame vào cache"""
        if frame_hash not in self.frame_cache:
            self.frame_cache[frame_hash] = frame_data

            # Giới hạn kích thước cache
            if len(self.frame_cache) > 200:  # Tăng kích thước cache
                oldest_key = next(iter(self.frame_cache))
                del self.frame_cache[oldest_key]

    def update_cache_display(self):
        """Cập nhật hiển thị hiệu quả cache"""
        total = self.cache_hits + self.cache_misses
        if total > 0:
            hit_rate = (self.cache_hits / total) * 100
            self.cacheLabel.config(text=f"Cache: {hit_rate:.1f}%")

            if hit_rate > 80:
                self.cacheLabel.config(fg="green")
            elif hit_rate > 60:
                self.cacheLabel.config(fg="orange")
            else:
                self.cacheLabel.config(fg="red")

    # REAL-TIME FRAME RECEIVER
    def startFrameReceiver(self):
        """Start receiving frames immediately"""
        if not self.isReceivingFrames and self.rtpSocket:
            self.isReceivingFrames = True
            self.statusLabel.config(text="Status: Receiving frames...")
            print("Starting to receive frames immediately...")

            self.frameReceiverThread = threading.Thread(target=self.receiveAndCacheFrames, daemon=True)
            self.frameReceiverThread.start()
            print("Frame receiver thread started!")

    def receiveAndCacheFrames(self):
        """Nhận frames liên tục - ĐÃ SỬA LỖI"""
        print("Starting to receive frames from server...")
        total_frames_received = 0
        last_log_time = time()

        while self.isReceivingFrames:
            try:
                # Giảm timeout để nhận frames nhanh hơn
                self.rtpSocket.settimeout(0.1)
                data, addr = self.rtpSocket.recvfrom(65536)

                if not data:
                    continue

                rtpPacket = RtpPacket()
                rtpPacket.decode(data)

                currFrameNbr = rtpPacket.seqNum()
                markerBit = rtpPacket.marker()
                payload = rtpPacket.getPayload()

                # DEBUG - Log received frames
                total_frames_received += 1
                current_time = time()
                if current_time - last_log_time >= 2.0:  # Log every 2 seconds
                    print(f"Received {total_frames_received} frames | Buffer: {len(self.frameBuffer)}/{self.bufferSize}")
                    last_log_time = current_time

                # Xử lý frame fragmentation
                if currFrameNbr != self.currentFrameNum:
                    self.rtpBuffer = b''  # Reset buffer for new frame
                    self.currentFrameNum = currFrameNbr

                self.rtpBuffer += payload

                # Khi frame hoàn chỉnh (marker bit = 1)
                if markerBit == 1:
                    # Tạo hash cho frame để caching
                    frame_hash = rtpPacket.getFrameHash()

                    # Cache frame mới
                    if frame_hash not in self.frame_cache:
                        self.cache_frame(frame_hash, self.rtpBuffer)

                    # LUÔN LUÔN thêm vào buffer (không giới hạn khi SETUP)
                    # Chỉ giới hạn khi đang PLAYING để tránh tràn bộ nhớ
                    if self.state != self.PLAYING or len(self.frameBuffer) < self.bufferSize:
                        self.frameBuffer.append((currFrameNbr, self.rtpBuffer, frame_hash))
                        self.updateBufferLabel()

                        # Log khi buffer đầy
                        if len(self.frameBuffer) >= self.bufferSize and self.state != self.PLAYING:
                            print(f"Buffer full: {len(self.frameBuffer)}/{self.bufferSize} frames")

                    self.performance_stats['frames_received'] += 1
                    self.performance_stats['last_frame_time'] = time()

                    # Cập nhật cache display
                    if currFrameNbr % 10 == 0:
                        self.update_cache_display()

                    self.rtpBuffer = b''

            except socket.timeout:
                # Timeout is normal, continue loop
                continue
            except Exception as e:
                if self.isReceivingFrames:
                    print(f"Error receiving frame: {e}")
                    traceback.print_exc()
                break

        print("Stopped receiving frames")

    def stopFrameReceiver(self):
        """Stop receiving frames"""
        self.isReceivingFrames = False

    # HỆ THỐNG PHÁT VIDEO
    def startPlayback(self):
        """Bắt đầu phát video từ buffer"""
        if not self.isPlaying:
            self.isPlaying = True
            self.playEvent.clear()
            self.statusLabel.config(text="Status: Playing...")
            print(f"Starting video playback with {len(self.frameBuffer)} frames in buffer...")

            self.playbackThread = threading.Thread(target=self.playFromBuffer, daemon=True)
            self.playbackThread.start()

    def stopPlayback(self):
        """Dừng phát video"""
        self.isPlaying = False
        self.playEvent.set()
        self.statusLabel.config(text="Status: Paused")

    def playFromBuffer(self):
        """Phát video từ buffer"""
        print("Starting playback from buffer...")
        consecutive_empty_cycles = 0

        while self.isPlaying and not self.playEvent.is_set():
            currentTime = time()
            elapsed = currentTime - self.lastDisplayTime

            # Adaptive frame rate dựa trên buffer level
            current_buffer = len(self.frameBuffer)
            adaptive_interval = self.frameInterval

            if current_buffer < 10:  # Buffer rất thấp
                adaptive_interval *= 1.8  # Giảm tốc độ phát
            elif current_buffer < 30:  # Buffer thấp
                adaptive_interval *= 1.3
            elif current_buffer > 80:  # Buffer cao
                adaptive_interval *= 0.9  # Tăng tốc độ phát

            if elapsed >= adaptive_interval:
                if self.frameBuffer:
                    frameNbr, frame_data, frame_hash = self.frameBuffer.popleft()
                    self.updateBufferLabel()

                    # Ưu tiên sử dụng frame từ cache
                    cached_frame = self.get_cached_frame(frame_hash)
                    if cached_frame:
                        self.performance_stats['frames_from_cache'] += 1
                        frame_data = cached_frame

                    # Hiển thị frame
                    cachename = self.writeFrame(frame_data)
                    self.updateMovie(cachename)
                    self.frameNbr = frameNbr

                    self.lastDisplayTime = currentTime
                    consecutive_empty_cycles = 0

                    # Log tiến độ phát
                    if frameNbr % 50 == 0:
                        buffer_level = len(self.frameBuffer)
                        print(f"Playing frame {frameNbr} | Buffer: {buffer_level}/{self.bufferSize}")

                else:
                    consecutive_empty_cycles += 1
                    if consecutive_empty_cycles >= 3:
                        print("Buffer empty, waiting for frames...")
                        self.statusLabel.config(text="Status: Waiting for frames...")
                        threading.Event().wait(0.1)
                        consecutive_empty_cycles = 0
                    else:
                        # Bỏ qua frame này
                        self.frameDropCount += 1

            # Reduce CPU usage
            threading.Event().wait(0.001)

        print("Stopped video playback")

    def updateBufferLabel(self):
        """Cập nhật hiển thị trạng thái buffer"""
        bufferText = f"Buffer: {len(self.frameBuffer)}/{self.bufferSize}"
        self.bufferLabel.config(text=bufferText)

        buffer_ratio = len(self.frameBuffer) / self.bufferSize
        if buffer_ratio < 0.1:
            self.bufferLabel.config(fg="red")
            self.statusLabel.config(text="Status: Very low buffer - May stutter")
        elif buffer_ratio < 0.3:
            self.bufferLabel.config(fg="orange")
            self.statusLabel.config(text="Status: Low buffer")
        elif buffer_ratio < 0.7:
            self.bufferLabel.config(fg="blue")
            self.statusLabel.config(text="Status: Playing - Stable buffer")
        else:
            self.bufferLabel.config(fg="green")
            self.statusLabel.config(text="Status: Playing - Good buffer")

    # =========================================================================
    # ORIGINAL METHODS
    # =========================================================================

    def setupMovie(self):
        """SETUP - start receiving frames immediately"""
        if self.state == self.INIT:
            print(f"Sending SETUP request for file: {self.fileName}")
            self.sendRtspRequest(self.SETUP)

    def exitClient(self):
        """Clean up when exiting"""
        self.stopFrameReceiver()
        self.stopPlayback()
        if self.state != self.INIT:
            self.sendRtspRequest(self.TEARDOWN)
        self.playEvent.set()
        try:
            self.master.destroy()
        except:
            pass
        # Dọn dẹp cache khi thoát
        self.cleanup_cache()

    def pauseMovie(self):
        """PAUSE - only stop playback, continue receiving frames"""
        if self.state == self.PLAYING:
            print("Sending PAUSE request")
            self.sendRtspRequest(self.PAUSE)
            self.stopPlayback()

    def playMovie(self):
        """PLAY - continue playing from existing buffer"""
        if self.state == self.READY:
            print("Sending PLAY request")

            # Kiểm tra buffer trước khi phát
            buffer_count = len(self.frameBuffer)
            print(f"Current buffer: {buffer_count}/{self.bufferSize} frames")

            if buffer_count < 10:
                print("Warning: Low buffer, video may stutter")

            if not self.isReceivingFrames:
                print("Restarting frame receiver...")
                self.startFrameReceiver()

            self.startPlayback()
            self.sendRtspRequest(self.PLAY)

    def cleanup_cache(self):
        """Hiển thị thống kê cache khi thoát"""
        total_frames = self.cache_hits + self.cache_misses
        if total_frames > 0:
            efficiency = (self.cache_hits / total_frames) * 100
            print(f"Cache statistics: {efficiency:.1f}% efficiency")
            print(f"Cache hits: {self.cache_hits}, misses: {self.cache_misses}")
            print(f"Frames in cache: {len(self.frame_cache)}")
            print(f"Total frames received: {self.performance_stats['frames_received']}")
            print(f"Frames dropped: {self.frameDropCount}")

    def listenRtp(self):
        """Keep for compatibility"""
        while not self.playEvent.is_set():
            try:
                self.rtpSocket.settimeout(0.1)
                data, _ = self.rtpSocket.recvfrom(65536)
            except socket.timeout:
                continue
            except Exception:
                break

    def writeFrame(self, data):
        cachename = CACHE_FILE_NAME + str(self.sessionId) + CACHE_FILE_EXT
        try:
            with open(cachename, "wb") as file:
                file.write(data)
        except Exception as e:
            print("Failed to write cache file:", e)
        return cachename

    def updateMovie(self, imageFile):
        try:
            photo = ImageTk.PhotoImage(Image.open(imageFile))
            self.label.configure(image=photo, height=288)
            self.label.image = photo
        except Exception as e:
            print("Failed to update movie frame:", e)
            traceback.print_exc()

    def connectToServer(self):
        self.rtspSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.rtspSocket.connect((self.serverAddr, self.serverPort))
            print(f"Connected to RTSP server {self.serverAddr}:{self.serverPort}")
        except Exception as e:
            tkMessageBox.showwarning('Connection Failed', "Connection to '%s' failed: %s" % (self.serverAddr, e))

    def sendRtspRequest(self, requestCode):
        self.rtspSeq += 1

        if requestCode == self.SETUP:
            requestLine = f"SETUP {self.fileName} RTSP/1.0"
        elif requestCode == self.PLAY:
            requestLine = f"PLAY {self.fileName} RTSP/1.0"
        elif requestCode == self.PAUSE:
            requestLine = f"PAUSE {self.fileName} RTSP/1.0"
        elif requestCode == self.TEARDOWN:
            requestLine = f"TEARDOWN {self.fileName} RTSP/1.0"
        else:
            return

        request = requestLine + "\r\nCSeq: " + str(self.rtspSeq)
        if requestCode != self.SETUP:
            request += "\r\nSession: " + str(self.sessionId)
        else:
            request += "\r\nTransport: RTP/UDP; client_port=" + str(self.rtpPort)

        self.requestSent = requestCode

        # validate transitions
        valid = False
        if requestCode == self.SETUP and self.state == self.INIT:
            valid = True
        elif requestCode == self.PLAY and self.state == self.READY:
            valid = True
        elif requestCode == self.PAUSE and self.state == self.PLAYING:
            valid = True
        elif requestCode == self.TEARDOWN and self.state != self.INIT:
            valid = True

        if not valid:
            self.rtspSeq -= 1
            print("Invalid RTSP state transition; request ignored.")
            return

        if requestCode == self.SETUP:
            threading.Thread(target=self.recvRtspReply, daemon=True).start()

        try:
            self.rtspSocket.sendall(request.encode("utf-8"))
            print('Data sent:\n' + request)
        except Exception as e:
            print("Failed to send RTSP request:", e)
            traceback.print_exc()

    def recvRtspReply(self):
        while True:
            try:
                reply = self.rtspSocket.recv(4096)
                if not reply:
                    break
            except Exception as e:
                print("RTSP recv exception:", e)
                break

            try:
                self.parseRtspReply(reply.decode("utf-8"))
            except Exception as e:
                print("Failed parsing RTSP reply:", e)
                traceback.print_exc()

            if self.requestSent == self.TEARDOWN and self.teardownAcked == 1:
                try:
                    self.rtspSocket.close()
                except:
                    pass
                break

    def parseRtspReply(self, data):
        print("=" * 50)
        print("Server Reply:")
        print(data)
        print("=" * 50)

        lines = data.splitlines()
        if len(lines) < 1:
            print("Empty RTSP reply.")
            return

        status_parts = lines[0].split(' ', 2)
        print(f"Status line parts: {status_parts}")

        if len(status_parts) < 2:
            print("Malformed status line:", lines[0])
            return

        try:
            status_code = int(status_parts[1])
            print(f"Status code: {status_code}")

            if status_code == 404:
                print("ERROR 404: File not found on server!")

        except:
            print("Could not parse status code:", status_parts)
            return

        seqNum = None
        session = None
        for line in lines[1:]:
            if line.lower().startswith("cseq"):
                try:
                    seqNum = int(line.split(':', 1)[1].strip())
                    print(f"CSeq: {seqNum}")
                except:
                    pass
            elif line.lower().startswith("session"):
                try:
                    session = int(line.split(':', 1)[1].strip())
                    print(f"Session: {session}")
                except:
                    pass

        if seqNum is None:
            print("CSeq not found in reply.")
            return

        if seqNum == self.rtspSeq:
            if self.sessionId == 0 and session is not None:
                self.sessionId = session

            if session is not None and self.sessionId != session:
                print(f"Session ID mismatch: received {session}, expected {self.sessionId}")
                return

            if status_code == 200:
                if self.requestSent == self.SETUP:
                    self.state = self.READY
                    print("RTSP State: READY")
                    self.openRtpPort()
                    # Bắt đầu nhận frames NGAY SAU SETUP
                    self.startFrameReceiver()
                elif self.requestSent == self.PLAY:
                    self.state = self.PLAYING
                    print("RTSP State: PLAYING")
                elif self.requestSent == self.PAUSE:
                    self.state = self.READY
                    print("RTSP State: READY (paused)")
                elif self.requestSent == self.TEARDOWN:
                    self.state = self.INIT
                    print("RTSP State: INIT (teardown)")
                    self.teardownAcked = 1
            else:
                print(f"RTSP Error: status code {status_code}")

    def openRtpPort(self):
        self.rtpSocket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.rtpSocket.settimeout(0.5)
        try:
            self.rtpSocket.bind(('', self.rtpPort))
            print(f"RTP Port opened at: {self.rtpPort}")
        except Exception as e:
            tkMessageBox.showwarning('Unable to Bind', 'Unable to bind RTP PORT=%d: %s' % (self.rtpPort, e))

    def handler(self):
        try:
            if self.state == self.PLAYING:
                self.pauseMovie()
        except:
            pass

        if tkMessageBox.askokcancel("Quit?", "Are you sure you want to quit?"):
            self.exitClient()
        else:
            pass


if __name__ == "__main__":
    from tkinter import Tk

    # Default connection information
    serverAddr = "127.0.0.1"
    serverPort = 554
    rtpPort = 25000
    fileName = "movie.Mjpeg"

    root = Tk()
    app = Client(root, serverAddr, serverPort, rtpPort, fileName)
    app.master.title("RTPClient with Client-Side Caching")
    root.mainloop()