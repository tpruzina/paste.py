# Copyright 2013 Carey Underwood <cwillu@cwillu.com>
#
# Permission is hereby granted, free of charge, to any person obtaining
# a copy of this software and associated documentation files (the
# "Software"), to deal in the Software without restriction, including
# without limitation the rights to use, copy, modify, merge, publish,
# distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so, subject to
# the following conditions:
# 
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
# 
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE
# LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION
# WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.


import sys
import time
import datetime
import string

from twisted.internet import protocol, reactor
from twisted.web import server
from twisted.web.resource import Resource, NoResource

import jinja2

jinja = jinja2.Environment(autoescape=True)

translation = string.maketrans(''.join(map(chr, range(0, 32) + range(127, 256))), '.' * (33+128))

def filter_binary(chunk):
  try:
    return chunk.decode('utf-8')
  except UnicodeDecodeError:
    out = ['\n']
    for x, y in zip(range(0, len(chunk), 32), range(32, len(chunk), 32) + [None]):
      raw = chunk[x:y]
      piece = raw.encode('hex')
      for a, b, c, d in zip(piece[::4], piece[1::4], piece[2::4], piece[3::4]):
        out.append(a+b+c+d)
      out.append(raw.translate(translation))
      out.append('\n')
    return ' '.join(out)

jinja.filters['filter_binary'] = filter_binary

stale = []
seen = []
pastes = {}
expire_time = 360000
expire_hours = expire_time / 3600
expire_minutes = expire_time % 3600 / 60
expire_seconds = expire_time % 60
expire_string = "%02d hours" % (expire_hours,)

udp_buffers = {}
class UDPReceiver(protocol.DatagramProtocol):
  def startProtocol(self):
    pass

  def datagramReceived(self, datagram, addr):
    now = time.time()
    if addr[0] not in udp_buffers or addr[1] not in udp_buffers[addr[0]]:
      print time.ctime() + " Connection from %s" % (addr,)
      host_udp_buffers = udp_buffers.setdefault(addr[0], {})
      buffer, last_len, buffer_len = host_udp_buffers.setdefault(addr[1], [[], 0, 0])

      host_pastes = pastes.setdefault(addr[0], [])
      host_pastes.append(buffer)

      self.transport.write("Connection made.  Contents available at\n", addr)
      self.transport.write("http://cwillu.com:8080/%s/%s\n" % (addr[0], len(host_pastes)), addr)
    else:
      buffer, last_len, buffer_len = udp_buffers[addr[0]][addr[1]]
   
    buffer.append(datagram)
    buffer_len = buffer_len + len(datagram)
    if addr[0] not in seen:
      seen.append(addr[0])
    if now - last_len > 1:
      last_len = now
      hours = now / 3600
      minutes = now % 3600 / 60
      seconds = now % 60

      timestamp = "%02d:%02d:%02d" % (hours, minutes, seconds)

      len_string = str(buffer_len)
      len_list = []
      while len_string:
        len_list.append(len_string[-3:])
        len_string = len_string[:-3]
      
      len_string = ' '.join(reversed(len_list))

      self.transport.write("%s   %s bytes\r" % (timestamp, len_string), addr)

    udp_buffers[addr[0]][addr[1]] = buffer, last_len, buffer_len


class Receiver(protocol.Protocol):
  def connectionMade(self):
    self.peer = self.transport.getPeer()

    host_pastes = pastes.setdefault(self.peer.host, [])

    print time.ctime() + " Connection from %s" % (self.peer,)
    
    self.buffer = []
    host_pastes.append(self.buffer)
    self.last_len = 0
    self.len = 0
    
    self.connected_time = time.time()

    self.transport.write("Connection made.  Contents available at\n")
    self.transport.write("http://cwillu.com:8080/%s/%s\n" % (self.peer.host, len(host_pastes)))
#    self.transport.write("Will expire %s after disconnect\n" % (expire_string,))
    
    self.running = True
    self.clean = True
    reactor.callLater(1, self.send_status, loop=True)

  def dataReceived(self, data):
    while self.peer.host in seen:
      seen.remove(self.peer.host)
      
    while self.peer.host in stale:
      stale.remove(self.peer.host)

    self.buffer.append(data)
    self.len += len(data)

    if self.clean:
      self.send_status()
      
    self.clean = False
    
  def send_status(self, loop=False):
    if not self.running:
      return
    reactor.callLater(1, self.send_status, loop=True)

    if self.last_len == self.len:
      self.clean = True
    self.last_len = self.len

    now = int(time.time() - self.connected_time)

    hours = now / 3600
    minutes = now % 3600 / 60
    seconds = now % 60

    timestamp = "%02d:%02d:%02d" % (hours, minutes, seconds)

    len_string = str(self.len)
    len_list = []
    while len_string:
      len_list.append(len_string[-3:])
      len_string = len_string[:-3]
      
    len_string = ' '.join(reversed(len_list))

    self.transport.write("%s   %s bytes\r" % (timestamp, len_string))

  def connectionLost(self, reason):
    self.transport.write('\n')
    seen.append(self.peer.host)
    self.running = False
    print time.ctime() + " Connection from %s closed; %s bytes received" % (self.peer, self.len)

def clean():
  while stale:
    host = stale.pop(0)
    if host in seen:
      continue
    print time.ctime() + " Expiring %s" % host
    pastes.pop(host)
    udp_buffers.pop(host)
    
  while seen:
    stale.append(seen.pop(0))    
  reactor.callLater(expire_time, clean)
clean()
reactor.callLater(expire_time, clean)

class ReceiverFactory(protocol.Factory):
  protocol = Receiver

class Top(Resource):
  def getChild(self, host, request):
    print time.ctime(), '-', host, '-', request
    if host not in pastes:
      return NoResource()
    return Paste(host)

class Paste(Resource):
  def __init__(self, host, id=None):
    Resource.__init__(self)
    self.host = host
    self.id = id

  def getChild(self, id, request):
    if self.id:
      return NoResource()
    try:
      id = int(id)
    except ValueError:
      return NoResource()
    
    if id < 1 or id > (len(pastes[self.host])):
      return NoResource()

    return Paste(self.host, id)
  
  def render_GET(self, request):
    if self.id:
      host_pastes = [pastes[self.host][self.id - 1]]
    else:
      host_pastes = pastes[self.host]
     
    template = jinja.from_string('''
      <a href="/{{ host }}">All recent pastes from this IP</a><br>
      {%- if pastes | length > 1 %}
        {%- for paste in pastes -%}
          <a href="#{{ loop.index0 + start }}">{{ loop.index0 + start }}</a><br>
        {%- endfor -%}
      {% endif %}
      {%- for paste in pastes -%}
        <hr>
        <a name="{{ loop.index0 + start }}" href="/{{ host }}/{{ loop.index0 + start }}"><h2>{{ loop.index0 + start }}</h2></a><pre>
        {%- if paste | length > 20 and pastes | length > 1 -%}
            <a href="/{{ host }}/{{ loop.index0 + start }}">Large file</a>
        {%- else -%}
          {%- for chunk in paste %}
            {{- chunk | filter_binary}}
          {%- endfor %}</pre>
        {%- endif %}
      {%- endfor -%}
    ''')
    return template.render(pastes=host_pastes, start=self.id or 1, host=self.host).encode('utf-8')

reactor.listenTCP(8080, server.Site(Top()))
reactor.listenTCP(10101, ReceiverFactory())
reactor.listenUDP(10101, UDPReceiver())
reactor.run()
