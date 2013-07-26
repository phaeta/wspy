#!/usr/bin/env python
import sys
from os.path import abspath, dirname

basepath = abspath(dirname(abspath(__file__)) + '/..')
sys.path.insert(0, basepath)

from websocket import websocket
from connection import Connection
from message import TextMessage
from errors import SocketClosed

ADDR = ('localhost', 8000)


class EchoClient(Connection):
    def onopen(self):
        print 'Connection established, sending "foo"'
        self.send(TextMessage('foo'))

    def onmessage(self, msg):
        print 'Received', msg
        raise SocketClosed(None, 'response received')

    def onerror(self, e):
        print 'Error:', e

    def onclose(self, code, reason):
        print 'Connection closed'


if __name__ == '__main__':
    print 'Connecting to ws://%s:%d' % ADDR
    sock = websocket()
    sock.connect(ADDR)
    EchoClient(sock).receive_forever()
