# -*- test-case-name: twisted.test.test_ident -*-
# Copyright (c) 2001-2004 Twisted Matrix Laboratories.
# See LICENSE for details.


"""
Ident protocol implementation.

API Stability: Unstable

@author: U{Jp Calderone<mailto:exarkun@twistedmatrix.com>}
"""

from __future__ import generators

import struct

from twisted.internet import defer
from twisted.protocols import basic
from twisted.python import log

class IdentError(Exception):
    """
    Can't determine connection owner; reason unknown.
    """
    
    identDescription = 'UNKNOWN-ERROR'

    def __str__(self):
        return self.identDescription


class NoUser(IdentError):
    """
    The connection specified by the port pair is not currently in use or
    currently not owned by an identifiable entity.
    """
    identDescription = 'NO-USER'


class InvalidPort(IdentError):
    """
    Either the local or foreign port was improperly specified. This should
    be returned if either or both of the port ids were out of range (TCP
    port numbers are from 1-65535), negative integers, reals or in any
    fashion not recognized as a non-negative integer.
    """
    identDescription = 'INVALID-PORT'


class HiddenUser(IdentError):
    """
    The server was able to identify the user of this port, but the
    information was not returned at the request of the user.
    """
    identDescription = 'HIDDEN-USER'


class IdentServer(basic.LineOnlyReceiver):
    """
    The Identification Protocol (a.k.a., "ident", a.k.a., "the Ident
    Protocol") provides a means to determine the identity of a user of a
    particular TCP connection. Given a TCP port number pair, it returns a
    character string which identifies the owner of that connection on the
    server's system.
    
    Server authors should subclass this class and override the lookup method.
    The default implementation returns an UNKNOWN-ERROR response for every
    query.
    """

    def lineReceived(self, line):
        parts = line.split(',')
        if len(parts) != 2:
            self.invalidQuery()
        else:
            try:
                portOnServer, portOnClient = map(int, parts)
            except ValueError:
                self.invalidQuery()
            else:
                self.validQuery(portOnServer, portOnClient)
    
    def invalidQuery(self):
        self.transport.loseConnection()
    
    def validQuery(self, portOnServer, portOnClient):
        serverAddr = self.transport.getHost()[1], portOnServer
        clientAddr = self.transport.getPeer()[1], portOnClient
        defer.maybeDeferred(self.lookup, serverAddr, clientAddr
            ).addCallback(self._cbLookup, portOnServer, portOnClient
            ).addErrback(self._ebLookup, portOnServer, portOnClient
            )
    
    def _cbLookup(self, (sysName, userId), sport, cport):
        self.sendLine('%d, %d : USERID : %s : %s' % (sport, cport, sysName, userId))

    def _ebLookup(self, failure, sport, cport):
        if failure.check(IdentError):
            self.sendLine('%d, %d : ERROR : %s' % (sport, cport, failure.value))
        else:
            log.err(failure)
            self.sendLine('%d, %d : ERROR : %s' % (sport, cport, IdentError(failure.value)))
 
    def lookup(self, serverAddress, clientAddress):
        """Lookup user information about the specified address pair.
        
        Return value should be a two-tuple of system name and username. 
        Acceptable values for the system name may be found online at

            <http://www.iana.org/assignments/operating-system-names>
        
        This method may also raise any IdentError subclass (or IdentError
        itself) to indicate user information will not be provided for the
        given query.
        
        A Deferred may also be returned.

        @param serverAddress: A two-tuple representing the server endpoint
        of the address being queried.  The first element is a string holding
        a dotted-quad IP address.  The second element is an integer
        representing the port.

        @param clientAddress: Like L{serverAddress}, but represents the
        client endpoint of the address being queried.
        """
        raise IdentError()

class ProcServerMixin:
    """Implements lookup() to grab entries for responses from /proc/net/tcp
    """

    SYSTEM_NAME = 'LINUX'

    try:
        from pwd import getpwuid
        def getUsername(self, uid, getpwuid=getpwuid):
            return getpwuid(uid)[0]
        del getpwuid
    except ImportError:
        def getUsername(self, uid):
            raise IdentError()

    def entries(self):
        f = file('/proc/net/tcp')
        f.readline()
        for L in f:
            yield L.strip()

    def dottedQuadFromHexString(self, hexstr):
        return '.'.join(map(str, struct.unpack('4B', struct.pack('=L', int(hexstr, 16)))))

    def unpackAddress(self, packed):
        addr, port = packed.split(':')
        addr = self.dottedQuadFromHexString(addr)
        port = int(port, 16)
        return addr, port

    def parseLine(self, line):
        parts = line.strip().split()
        localAddr, localPort = self.unpackAddress(parts[1])
        remoteAddr, remotePort = self.unpackAddress(parts[2])
        uid = int(parts[7])
        return (localAddr, localPort), (remoteAddr, remotePort), uid

    def lookup(self, serverAddress, clientAddress):
        for ent in self.entries():
            localAddr, remoteAddr, uid = self.parseLine(ent)
            if remoteAddr == clientAddress and localAddr[1] == serverAddress[1]:
                return (self.SYSTEM_NAME, self.getUsername(uid))

        raise NoUser()


class IdentClient(basic.LineOnlyReceiver):

    errorTypes = (IdentError, NoUser, InvalidPort, HiddenUser)

    def __init__(self):
        self.queries = []
    
    def lookup(self, portOnServer, portOnClient):
        """Lookup user information about the specified address pair.
        """
        self.queries.append((defer.Deferred(), portOnServer, portOnClient))
        if len(self.queries) > 1:
            return self.queries[-1][0]
        
        self.sendLine('%d, %d' % (portOnServer, portOnClient))
        return self.queries[-1][0]

    def lineReceived(self, line):
        if not self.queries:
            log.msg("Unexpected server response: %r" % (line,))
        else:
            d, _, _ = self.queries.pop(0)
            self.parseResponse(d, line)
            if self.queries:
                self.sendLine('%d, %d' % (self.queries[0][1], self.queries[0][2]))

    def connectionLost(self, reason):
        for q in self.queries:
            q[0].errback(IdentError(reason))
        self.queries = []
    
    def parseResponse(self, deferred, line):
        parts = line.split(':', 2)
        if len(parts) != 3:
            deferred.errback(IdentError(line))
        else:
            ports, type, addInfo = map(str.strip, parts)
            if type == 'ERROR':
                for et in self.errorTypes:
                    if et.identDescription == addInfo:
                        deferred.errback(et(line))
                        return
                deferred.errback(IdentError(line))
            else:
                deferred.callback((type, addInfo))

__all__ = ['IdentError', 'NoUser', 'InvalidPort', 'HiddenUser',
           'IdentServer', 'IdentClient',
           'ProcServerMixin']
