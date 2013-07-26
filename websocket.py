import os
import re
import socket
import ssl
from hashlib import sha1
from base64 import b64encode

from frame import receive_frame
from errors import HandshakeError, SSLError


WS_GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'
WS_VERSION = '13'


def split_stripped(value, delim=','):
    return map(str.strip, str(value).split(delim)) if value else []


class websocket(object):
    """
    Implementation of web socket, upgrades a regular TCP socket to a websocket
    using the HTTP handshakes and frame (un)packing, as specified by RFC 6455.
    The API of a websocket is identical to that of a regular socket, as
    illustrated by the examples below.

    Server example:
    >>> import twspy, socket
    >>> sock = twspy.websocket()
    >>> sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    >>> sock.bind(('', 8000))
    >>> sock.listen()

    >>> client = sock.accept()
    >>> client.send(twspy.Frame(twspy.OPCODE_TEXT, 'Hello, Client!'))
    >>> frame = client.recv()

    Client example:
    >>> import twspy
    >>> sock = twspy.websocket()
    >>> sock.connect(('', 8000))
    >>> sock.send(twspy.Frame(twspy.OPCODE_TEXT, 'Hello, Server!'))
    """
    def __init__(self, sock=None, protocols=[], extensions=[], sfamily=socket.AF_INET,
                 sproto=0):
        """
        Create a regular TCP socket of family `family` and protocol

        `sock` is an optional regular TCP socket to be used for sending binary
        data. If not specified, a new socket is created.

        `protocols` is a list of supported protocol names.

        `extensions` is a list of supported extensions.

        `sfamily` and `sproto` are used for the regular socket constructor.
        """
        self.protocols = protocols
        self.extensions = extensions
        self.sock = sock or socket.socket(sfamily, socket.SOCK_STREAM, sproto)
        self.secure = False
        self.handshake_started = False

    def bind(self, address):
        self.sock.bind(address)

    def listen(self, backlog):
        self.sock.listen(backlog)

    def accept(self):
        """
        Equivalent to socket.accept(), but transforms the socket into a
        websocket instance and sends a server handshake (after receiving a
        client handshake). Note that the handshake may raise a HandshakeError
        exception.
        """
        sock, address = self.sock.accept()
        wsock = websocket(sock)
        wsock.server_handshake()
        return wsock, address

    def connect(self, address, path='/'):
        """
        Equivalent to socket.connect(), but sends an client handshake request
        after connecting.

        `address` is a (host, port) tuple of the server to connect to.

        `path` is optional, used as the *location* part of the HTTP handshake.
        In a URL, this would show as ws://host[:port]/path.
        """
        self.sock.connect(address)
        self.client_handshake(address, path)

    def send(self, *args):
        """
        Send a number of frames.
        """
        for frame in args:
            #print 'send frame:', frame, 'to %s:%d' % self.sock.getpeername()
            self.sock.sendall(frame.pack())

    def recv(self):
        """
        Receive a single frames. This can be either a data frame or a control
        frame.
        """
        frame = receive_frame(self.sock)
        #print 'receive frame:', frame, 'from %s:%d' % self.sock.getpeername()
        return frame

    def recvn(self, n):
        """
        Receive exactly `n` frames. These can be either data frames or control
        frames, or a combination of both.
        """
        return [self.recv() for i in xrange(n)]

    def getpeername(self):
        return self.sock.getpeername()

    def getsockname(self):
        return self.sock.getsockname()

    def setsockopt(self, level, optname, value):
        self.sock.setsockopt(level, optname, value)

    def getsockopt(self, level, optname):
        return self.sock.getsockopt(level, optname)

    def close(self):
        self.sock.close()

    def server_handshake(self):
        """
        Execute a handshake as the server end point of the socket. If the HTTP
        request headers sent by the client are invalid, a HandshakeError
        is raised.
        """
        def fail(msg):
            self.sock.close()
            raise HandshakeError(msg)

        # Receive HTTP header
        raw_headers = ''

        while raw_headers[-4:] not in ('\r\n\r\n', '\n\n'):
            raw_headers += self.sock.recv(512).decode('utf-8', 'ignore')

        # Request must be HTTP (at least 1.1) GET request, find the location
        match = re.search(r'^GET (.*) HTTP/1.1\r\n', raw_headers)

        if match is None:
            fail('not a valid HTTP 1.1 GET request')

        location = match.group(1)
        headers = re.findall(r'(.*?): ?(.*?)\r\n', raw_headers)
        header_names = [name for name, value in headers]

        def header(name):
            return ', '.join([v for n, v in headers if n == name])

        # Check if headers that MUST be present are actually present
        for name in ('Host', 'Upgrade', 'Connection', 'Sec-WebSocket-Key',
                     'Sec-WebSocket-Version'):
            if name not in header_names:
                fail('missing "%s" header' % name)

        # Check WebSocket version used by client
        version = header('Sec-WebSocket-Version')

        if version != WS_VERSION:
            fail('WebSocket version %s requested (only %s '
                                 'is supported)' % (version, WS_VERSION))

        # Verify required header keywords
        if 'websocket' not in header('Upgrade').lower():
            fail('"Upgrade" header must contain "websocket"')

        if 'upgrade' not in header('Connection').lower():
            fail('"Connection" header must contain "Upgrade"')

        # Origin must be present if browser client
        if 'User-Agent' in header_names and 'Origin' not in header_names:
            fail('browser client must specify "Origin" header')

        # Only supported protocols are returned
        client_protocols = split_stripped(header('Sec-WebSocket-Extensions'))
        protocol = 'null'

        for p in client_protocols:
            if p in self.protocols:
                protocol = p
                break

        # Only supported extensions are returned
        extensions = split_stripped(header('Sec-WebSocket-Extensions'))
        extensions = [e for e in extensions if e in self.extensions]

        # Encode acceptation key using the WebSocket GUID
        key = header('Sec-WebSocket-Key').strip()
        accept = b64encode(sha1(key + WS_GUID).digest())

        # Construct HTTP response header
        shake = 'HTTP/1.1 101 Web Socket Protocol Handshake\r\n'
        shake += 'Upgrade: websocket\r\n'
        shake += 'Connection: Upgrade\r\n'
        shake += 'WebSocket-Origin: %s\r\n' % header('Origin')
        shake += 'WebSocket-Location: ws://%s%s\r\n' \
                 % (header('Host'), location)
        shake += 'Sec-WebSocket-Accept: %s\r\n' % accept
        shake += 'Sec-WebSocket-Protocol: %s\r\n' % protocol
        shake += 'Sec-WebSocket-Extensions: %s\r\n' % ', '.join(extensions)

        self.sock.sendall(shake + '\r\n')
        self.handshake_started = True

    def client_handshake(self, address, location):
        """
        Executes a handshake as the client end point of the socket. May raise a
        HandshakeError if the server response is invalid.
        """
        def fail(msg):
            self.sock.close()
            raise HandshakeError(msg)

        if len(location) == 0:
            fail('request location is empty')

        # Generate a 16-byte random base64-encoded key for this connection
        key = b64encode(os.urandom(16))

        # Send client handshake
        shake = 'GET %s HTTP/1.1\r\n' % location
        shake += 'Host: %s:%d\r\n' % address
        shake += 'Upgrade: websocket\r\n'
        shake += 'Connection: keep-alive, Upgrade\r\n'
        shake += 'Sec-WebSocket-Key: %s\r\n' % key
        shake += 'Origin: null\r\n'  # FIXME: is this correct/necessary?
        shake += 'Sec-WebSocket-Version: %s\r\n' % WS_VERSION

        # These are for eagerly caching webservers
        shake += 'Pragma: no-cache\r\n'
        shake += 'Cache-Control: no-cache\r\n'

        # Request protocols and extension, these are later checked with the
        # actual supported values from the server's response
        if self.protocols:
            shake += 'Sec-WebSocket-Protocol: %s\r\n' % ', '.join(self.protocols)

        if self.extensions:
            shake += 'Sec-WebSocket-Extensions: %s\r\n' % ', '.join(self.extensions)

        self.sock.sendall(shake + '\r\n')
        self.handshake_started = True

        # Receive and process server handshake
        raw_headers = ''

        while raw_headers[-4:] not in ('\r\n\r\n', '\n\n'):
            raw_headers += self.sock.recv(512).decode('utf-8', 'ignore')

        # Response must be HTTP (at least 1.1) with status 101
        if not raw_headers.startswith('HTTP/1.1 101'):
            # TODO: implement HTTP authentication (401) and redirect (3xx)?
            fail('not a valid HTTP 1.1 status 101 response')

        headers = re.findall(r'(.*?): ?(.*?)\r\n', raw_headers)
        header_names = [name for name, value in headers]

        def header(name):
            return ', '.join([v for n, v in headers if n == name])

        # Check if headers that MUST be present are actually present
        for name in ('Upgrade', 'Connection', 'Sec-WebSocket-Accept'):
            if name not in header_names:
                fail('missing "%s" header' % name)

        if 'websocket' not in header('Upgrade').lower():
            fail('"Upgrade" header must contain "websocket"')

        if 'upgrade' not in header('Connection').lower():
            fail('"Connection" header must contain "Upgrade"')

        # Verify accept header
        accept = header('Sec-WebSocket-Accept').strip()
        required_accept = b64encode(sha1(key + WS_GUID).digest())

        if accept != required_accept:
            fail('invalid websocket accept header "%s"' % accept)

        # Compare extensions
        server_extensions = split_stripped(header('Sec-WebSocket-Extensions'))

        for ext in server_extensions:
            if ext not in self.extensions:
                fail('server extension "%s" is unsupported by client' % ext)

        for ext in self.extensions:
            if ext not in server_extensions:
                fail('client extension "%s" is unsupported by server' % ext)

        # Assert that returned protocol is supported
        protocol = header('Sec-WebSocket-Protocol')

        if protocol:
            if protocol != 'null' and protocol not in self.protocols:
                fail('unsupported protocol "%s"' % protocol)

            self.protocol = protocol

    def enable_ssl(self, *args, **kwargs):
        """
        Transforms the regular socket.socket to an ssl.SSLSocket for secure
        connections. Any arguments are passed to ssl.wrap_socket:
        http://docs.python.org/dev/library/ssl.html#ssl.wrap_socket
        """
        if self.handshake_started:
            raise SSLError('can only enable SSL before handshake')

        self.secure = True
        self.sock = ssl.wrap_socket(self.sock, *args, **kwargs)
