import sys
from time import time
from tkinter import *
import tkinter.messagebox as tkMessageBox
from PIL import Image, ImageTk
import socket, threading, sys, traceback, os
from RtpPacket import RtpPacket

CACHE_FILE_NAME = "cache-"
CACHE_FILE_EXT = ".jpg"

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

        # connection params
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

        # frame/state tracking
        self.frameNbr = 0
        self.rtpBuffer = b''       # buffer for reassembling fragmented frame
        self.prevSeqNum = 0

        # event to stop RTP listening loop
        self.playEvent = threading.Event()
        self.playEvent.clear()

        # sockets (initialized later)
        self.rtspSocket = None
        self.rtpSocket = None

        self.connectToServer()

    def createWidgets(self):
        """Build GUI."""
        self.setup = Button(self.master, width=20, padx=3, pady=3, text="Setup", command=self.setupMovie)
        self.setup.grid(row=1, column=0, padx=2, pady=2)

        self.start = Button(self.master, width=20, padx=3, pady=3, text="Play", command=self.playMovie)
        self.start.grid(row=1, column=1, padx=2, pady=2)

        self.pause = Button(self.master, width=20, padx=3, pady=3, text="Pause", command=self.pauseMovie)
        self.pause.grid(row=1, column=2, padx=2, pady=2)

        self.teardown = Button(self.master, width=20, padx=3, pady=3, text="Teardown", command=self.exitClient)
        self.teardown.grid(row=1, column=3, padx=2, pady=2)

        self.label = Label(self.master, height=19)
        self.label.grid(row=0, column=0, columnspan=4, sticky=W+E+N+S, padx=5, pady=5)

    def setupMovie(self):
        if self.state == self.INIT:
            self.sendRtspRequest(self.SETUP)

    def exitClient(self):
        """Send TEARDOWN and close GUI."""
        if self.state != self.INIT:
            self.sendRtspRequest(self.TEARDOWN)

        # ensure RTP listener stops
        self.playEvent.set()

        # close GUI
        try:
            self.master.destroy()
        except:
            pass

        # remove cache file
        try:
            os.remove(CACHE_FILE_NAME + str(self.sessionId) + CACHE_FILE_EXT)
        except:
            pass

    def pauseMovie(self):
        if self.state == self.PLAYING:
            self.sendRtspRequest(self.PAUSE)

    def playMovie(self):
        if self.state == self.READY:
            # start RTP listening thread (daemon)
            t = threading.Thread(target=self.listenRtp, daemon=True)
            t.start()

            # clear stop flag and send PLAY
            self.playEvent.clear()
            self.sendRtspRequest(self.PLAY)

    def listenRtp(self):
        """Listen for RTP packets and reassemble fragmented frames using marker bit."""
        while True:
            try:
                data, _ = self.rtpSocket.recvfrom(65536)  # larger buffer
                if not data:
                    continue

                rtpPacket = RtpPacket()
                rtpPacket.decode(data)

                currFrameNbr = rtpPacket.seqNum()
                markerBit = rtpPacket.marker()
                # debug
                # print("Current Seq Num:", currFrameNbr, "Marker:", markerBit)

                payload = rtpPacket.getPayload()

                # if new sequence number (in-order)
                if currFrameNbr > self.prevSeqNum:
                    # detect lost packets
                    if currFrameNbr > self.prevSeqNum + 1 and self.prevSeqNum != 0:
                        print("Packet loss detected: expected", self.prevSeqNum + 1, "got", currFrameNbr)
                        # reset buffer if jumping to new frame
                        self.rtpBuffer = b''

                    # new frame starts — reset buffer then append
                    if currFrameNbr != self.prevSeqNum:
                        self.rtpBuffer = b''

                    self.prevSeqNum = currFrameNbr

                # append payload (works for both single-chunk and fragmented frames)
                self.rtpBuffer += payload

                # if marker bit set -> last chunk of frame => assemble and display
                if markerBit == 1:
                    self.frameNbr = currFrameNbr
                    cachename = self.writeFrame(self.rtpBuffer)
                    self.updateMovie(cachename)
                    # reset buffer for next frame
                    self.rtpBuffer = b''

            except socket.timeout:
                # normal: loop back and check events
                pass
            except OSError as e:
                # socket closed or other OS error -> break
                print("RTP listen OSError:", e)
                break
            except Exception as e:
                print("RTP listen exception:", e)
                traceback.print_exc()
                break

            # stop conditions
            if self.playEvent.is_set():
                break
            if self.teardownAcked == 1:
                # close socket and exit
                try:
                    self.rtpSocket.close()
                except:
                    pass
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
            print("Connected to RTSP server", self.serverAddr, ":", self.serverPort)
        except Exception as e:
            tkMessageBox.showwarning('Connection Failed', "Connection to '%s' failed: %s" % (self.serverAddr, e))

    def sendRtspRequest(self, requestCode):
        """Send RTSP request to the server."""
        # 1. increment sequence
        self.rtspSeq += 1

        # 2. request line
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

        # build request (CRLF terminated)
        request = requestLine + "\r\nCSeq: " + str(self.rtspSeq)
        if requestCode != self.SETUP:
            request += "\r\nSession: " + str(self.sessionId)
        else:
            request += "\r\nTransport: RTP/UDP; client_port=" + str(self.rtpPort)

        self.requestSent = requestCode

        # validate transitions (simple)
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
            # invalid transition: rollback seq and ignore
            self.rtspSeq -= 1
            print("Invalid RTSP state transition; request ignored.")
            return

        # start listener for RTSP replies on SETUP (daemon)
        if requestCode == self.SETUP:
            threading.Thread(target=self.recvRtspReply, daemon=True).start()

        try:
            self.rtspSocket.sendall(request.encode("utf-8"))
            print('\nData sent:\n' + request)
        except Exception as e:
            print("Failed to send RTSP request:", e)
            traceback.print_exc()

    def recvRtspReply(self):
        """Receive RTSP reply from the server."""
        while True:
            try:
                reply = self.rtspSocket.recv(4096)
                if not reply:
                    # connection closed
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
        """Parse the RTSP reply from the server."""
        print("-" * 20 + "\nServer Reply:\n" + data + "\n" + "-" * 20)
        lines = data.splitlines()
        if len(lines) < 1:
            print("Empty RTSP reply.")
            return

        # status line
        status_parts = lines[0].split(' ', 2)
        if len(status_parts) < 2:
            print("Malformed status line:", lines[0])
            return

        try:
            status_code = int(status_parts[1])
        except:
            print("Could not parse status code:", status_parts)
            return

        seqNum = None
        session = None
        for line in lines[1:]:
            if line.lower().startswith("cseq"):
                try:
                    seqNum = int(line.split(':', 1)[1].strip())
                except:
                    pass
            elif line.lower().startswith("session"):
                try:
                    session = int(line.split(':', 1)[1].strip())
                except:
                    pass

        if seqNum is None:
            print("CSeq not found in reply.")
            return

        if seqNum == self.rtspSeq:
            if self.sessionId == 0 and session is not None:
                self.sessionId = session

            if session is not None and self.sessionId != session:
                print("Session ID mismatch: received", session, "expected", self.sessionId)
                return

            if status_code == 200:
                if self.requestSent == self.SETUP:
                    self.state = self.READY
                    print("RTSP State: READY")
                    self.openRtpPort()
                elif self.requestSent == self.PLAY:
                    self.state = self.PLAYING
                    print("RTSP State: PLAYING")
                elif self.requestSent == self.PAUSE:
                    self.state = self.READY
                    print("RTSP State: READY (paused)")
                    # signal RTP thread to stop sending/receiving
                    self.playEvent.set()
                elif self.requestSent == self.TEARDOWN:
                    self.state = self.INIT
                    print("RTSP State: INIT (teardown)")
                    self.teardownAcked = 1
            else:
                print("RTSP Error: status code", status_code)

    def openRtpPort(self):
        """Open RTP socket bound to the client rtpPort."""
        self.rtpSocket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.rtpSocket.settimeout(0.5)
        try:
            self.rtpSocket.bind(('', self.rtpPort))
            print("RTP Port opened at:", self.rtpPort)
        except Exception as e:
            tkMessageBox.showwarning('Unable to Bind', 'Unable to bind RTP PORT=%d: %s' % (self.rtpPort, e))

    def handler(self):
        """Handler when user tries to close window."""
        try:
            # attempt to pause if playing
            if self.state == self.PLAYING:
                self.pauseMovie()
        except:
            pass

        if tkMessageBox.askokcancel("Quit?", "Are you sure you want to quit?"):
            self.exitClient()
        else:
            # do nothing (do not auto-resume)
            pass
