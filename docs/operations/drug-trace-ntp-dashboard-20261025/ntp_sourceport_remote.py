import json
import socket
import struct
import time

out=[]
for host in ['<relay-net-host>','<relay-net-host>']:
    try:
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as s:
            s.settimeout(3);s.bind(('<prod-host>',123))
            packet=bytearray(48);packet[0]=0x23
            now=time.time();packet[40:48]=struct.pack('!II',int(now)+2208988800,int(now%1*2**32))
            s.sendto(packet,(host,123));data,peer=s.recvfrom(512)
            out.append({'host':host,'source_port':123,'bytes':len(data),'stratum':data[1],
                        'valid_origin':data[24:32]==packet[40:48],'leap':data[0]>>6})
    except OSError as e:out.append({'host':host,'source_port':123,'error':type(e).__name__})
print('__MAINTENANCE__'+json.dumps(out)+'__END__')
