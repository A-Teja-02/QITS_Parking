"""
ONVIF support (standard library only - no extra packages).

- discover(): WS-Discovery probe on the local network; returns cameras/NVRs that answer.
- get_streams(): logs in to a camera (WS-Security UsernameToken digest), reads its
  device info and media profiles, and returns the RTSP URL for each profile.

Works with ONVIF-enabled Hikvision, Dahua, CP Plus, Uniview, Axis, Reolink, TP-Link, ...
(ONVIF must be enabled on the camera, often with a separate ONVIF user.)
"""
import base64
import datetime as dt
import hashlib
import os
import re
import socket
import time
import urllib.error
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlparse

WS_DISCOVERY_ADDR = ("239.255.255.250", 3702)
_PROBE = """<?xml version="1.0" encoding="UTF-8"?>
<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope"
 xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing"
 xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"
 xmlns:dn="http://www.onvif.org/ver10/network/wsdl">
 <e:Header>
  <w:MessageID>uuid:{mid}</w:MessageID>
  <w:To e:mustUnderstand="true">urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>
  <w:Action e:mustUnderstand="true">http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action>
 </e:Header>
 <e:Body><d:Probe><d:Types>dn:NetworkVideoTransmitter</d:Types></d:Probe></e:Body>
</e:Envelope>"""


def _local_ipv4() -> List[str]:
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except Exception:
        pass
    try:  # the adapter used for the default route
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    return [ip for ip in ips if not ip.startswith("127.")] or ["0.0.0.0"]


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find_all(root: ET.Element, name: str) -> List[ET.Element]:
    return [el for el in root.iter() if _local(el.tag) == name]


def _find_text(root: ET.Element, name: str) -> Optional[str]:
    for el in root.iter():
        if _local(el.tag) == name and el.text and el.text.strip():
            return el.text.strip()
    return None


# ----------------------------------------------------------------------
# Discovery
# ----------------------------------------------------------------------
def discover(timeout: float = 3.0) -> List[Dict[str, Any]]:
    found: Dict[str, Dict[str, Any]] = {}
    msg = _PROBE.format(mid=uuid.uuid4()).encode()
    socks = []
    for ip in _local_ipv4():
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
            if ip != "0.0.0.0":
                s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(ip))
                s.bind((ip, 0))
            s.settimeout(0.3)
            s.sendto(msg, WS_DISCOVERY_ADDR)
            socks.append(s)
        except Exception:
            continue
    end = time.time() + timeout
    while time.time() < end and socks:
        for s in socks:
            try:
                data, addr = s.recvfrom(65535)
            except socket.timeout:
                continue
            except Exception:
                continue
            try:
                root = ET.fromstring(data)
            except Exception:
                continue
            xaddrs = (_find_text(root, "XAddrs") or "").split()
            scopes = (_find_text(root, "Scopes") or "").split()
            if not xaddrs:
                continue
            ip = addr[0]
            info = {"ip": ip, "xaddrs": xaddrs, "name": None, "hardware": None, "location": None}
            for sc in scopes:
                for key in ("name", "hardware", "location"):
                    m = re.search(rf"onvif://www\.onvif\.org/{key}/(.+)$", sc)
                    if m:
                        info[key] = urllib.request.unquote(m.group(1)).replace("_", " ")
            port = urlparse(xaddrs[0]).port or 80
            info["port"] = port
            found[ip] = info
    for s in socks:
        s.close()
    return sorted(found.values(), key=lambda d: tuple(int(p) for p in d["ip"].split(".")))


# ----------------------------------------------------------------------
# SOAP calls
# ----------------------------------------------------------------------
class OnvifError(Exception):
    pass


class OnvifCamera:
    def __init__(self, host: str, port: int = 80, username: str = "", password: str = "", timeout: float = 6.0):
        self.host, self.port = host.strip(), int(port or 80)
        self.username, self.password = username or "", password or ""
        self.timeout = timeout
        self.device_url = f"http://{self.host}:{self.port}/onvif/device_service"
        self.time_offset = 0.0  # camera clock minus our clock (digest login uses the camera's time)

    def _security(self) -> str:
        if not self.username:
            return ""
        nonce = os.urandom(16)
        created = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=self.time_offset)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        digest = base64.b64encode(hashlib.sha1(nonce + created.encode() + self.password.encode()).digest()).decode()
        return (
            '<Security s:mustUnderstand="1" xmlns="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd">'
            f'<UsernameToken><Username>{self._esc(self.username)}</Username>'
            '<Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">'
            f'{digest}</Password>'
            '<Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">'
            f'{base64.b64encode(nonce).decode()}</Nonce>'
            f'<Created xmlns="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">{created}</Created>'
            '</UsernameToken></Security>')

    @staticmethod
    def _esc(v: str) -> str:
        return v.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def _call(self, url: str, body: str, auth: bool = True) -> ET.Element:
        env = ('<?xml version="1.0" encoding="UTF-8"?>'
               '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">'
               f'<s:Header>{self._security() if auth else ""}</s:Header><s:Body>{body}</s:Body></s:Envelope>')
        req = urllib.request.Request(url, data=env.encode(), method="POST",
                                     headers={"Content-Type": "application/soap+xml; charset=utf-8"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return ET.fromstring(r.read())
        except urllib.error.HTTPError as e:
            raw = e.read()
            reason = ""
            try:
                reason = _find_text(ET.fromstring(raw), "Text") or ""
            except Exception:
                pass
            if e.code in (400, 401) or "auth" in reason.lower() or "sender" in reason.lower():
                raise OnvifError("Camera rejected the ONVIF login - check the ONVIF username/password "
                                 "(on Hikvision/Dahua, ONVIF often needs its own user enabled in the camera settings).")
            raise OnvifError(f"Camera returned HTTP {e.code} {reason}".strip())
        except urllib.error.URLError as e:
            raise OnvifError(f"Could not reach {self.host}:{self.port} ({e.reason}). Check the IP, ONVIF port and that ONVIF is enabled.")
        except socket.timeout:
            raise OnvifError(f"{self.host}:{self.port} did not answer (timeout).")

    def sync_time(self):
        """Read the camera's clock (no login needed) so the digest timestamp matches it."""
        try:
            root = self._call(self.device_url, '<tds:GetSystemDateAndTime xmlns:tds="http://www.onvif.org/ver10/device/wsdl"/>', auth=False)
            utc = next((el for el in root.iter() if _local(el.tag) == "UTCDateTime"), None)
            if utc is None:
                return
            g = lambda n: int(_find_text(utc, n) or 0)
            cam = dt.datetime(g("Year"), g("Month"), g("Day"), g("Hour"), g("Minute"), g("Second"), tzinfo=dt.timezone.utc)
            self.time_offset = (cam - dt.datetime.now(dt.timezone.utc)).total_seconds()
        except Exception:
            self.time_offset = 0.0

    def _fix_host(self, url: str) -> str:
        """Cameras behind NAT / with several NICs may report an internal IP - use the one we reached."""
        try:
            p = urlparse(url)
            if p.hostname and p.hostname != self.host:
                netloc = self.host + (f":{p.port}" if p.port else "")
                return p._replace(netloc=netloc).geturl()
        except Exception:
            pass
        return url

    def device_info(self) -> Dict[str, Optional[str]]:
        root = self._call(self.device_url, '<tds:GetDeviceInformation xmlns:tds="http://www.onvif.org/ver10/device/wsdl"/>')
        return {k: _find_text(root, k) for k in ("Manufacturer", "Model", "FirmwareVersion", "SerialNumber")}

    def media_url(self) -> str:
        root = self._call(self.device_url, '<tds:GetCapabilities xmlns:tds="http://www.onvif.org/ver10/device/wsdl">'
                                           '<tds:Category>Media</tds:Category></tds:GetCapabilities>')
        for el in root.iter():
            if _local(el.tag) == "Media":
                x = _find_text(el, "XAddr")
                if x:
                    return self._fix_host(x)
        return f"http://{self.host}:{self.port}/onvif/media_service"

    def streams(self) -> List[Dict[str, Any]]:
        murl = self.media_url()
        root = self._call(murl, '<trt:GetProfiles xmlns:trt="http://www.onvif.org/ver10/media/wsdl"/>')
        out = []
        for prof in _find_all(root, "Profiles"):
            token = prof.attrib.get("token")
            if not token:
                continue
            res = next((el for el in prof.iter() if _local(el.tag) == "Resolution"), None)
            w = int(_find_text(res, "Width") or 0) if res is not None else 0
            h = int(_find_text(res, "Height") or 0) if res is not None else 0
            enc = None
            vec = next((el for el in prof.iter() if _local(el.tag) == "VideoEncoderConfiguration"), None)
            if vec is not None:
                enc = _find_text(vec, "Encoding")
            body = ('<trt:GetStreamUri xmlns:trt="http://www.onvif.org/ver10/media/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema">'
                    '<trt:StreamSetup><tt:Stream>RTP-Unicast</tt:Stream><tt:Transport><tt:Protocol>RTSP</tt:Protocol></tt:Transport></trt:StreamSetup>'
                    f'<trt:ProfileToken>{self._esc(token)}</trt:ProfileToken></trt:GetStreamUri>')
            try:
                uri = _find_text(self._call(murl, body), "Uri")
            except OnvifError:
                uri = None
            if not uri:
                continue
            uri = self._fix_host(uri)
            out.append({"token": token, "name": _find_text(prof, "Name") or token, "width": w, "height": h,
                        "encoding": enc, "rtsp_url_no_auth": uri, "rtsp_url": self._with_credentials(uri)})
        out.sort(key=lambda p: -(p["width"] * p["height"]))  # main (highest resolution) stream first
        return out

    def _with_credentials(self, uri: str) -> str:
        if not self.username or "@" in uri.split("//", 1)[-1].split("/", 1)[0]:
            return uri
        scheme, rest = uri.split("://", 1)
        return f"{scheme}://{quote(self.username, safe='')}:{quote(self.password, safe='')}@{rest}"


def get_streams(host: str, port: int = 80, username: str = "", password: str = "") -> Dict[str, Any]:
    cam = OnvifCamera(host, port, username, password)
    cam.sync_time()
    info: Dict[str, Optional[str]] = {}
    try:
        info = cam.device_info()
    except OnvifError:
        raise
    except Exception:
        info = {}
    streams = cam.streams()
    if not streams:
        raise OnvifError("Logged in, but the camera reported no RTSP streams.")
    return {"host": host, "port": port, "device": info, "clock_offset_seconds": round(cam.time_offset, 1), "streams": streams}
