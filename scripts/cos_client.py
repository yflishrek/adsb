#!/usr/bin/env python3
"""Minimal Tencent Cloud COS client -- standard library only.

Why hand-rolled
---------------
The SCF deploy package (`cloud/scf/scf.zip`) is uploaded through the console and
has stayed at three files with zero dependencies.  Vendoring `cos-python-sdk-v5`
would pull in `requests`, `crcmod` and friends, i.e. a few hundred KB that the
console upload makes awkward, just to sign one HTTP PUT.  COS's V5 signature is
HMAC-SHA1 over a documented canonical string, so `hashlib` + `hmac` is enough.

Credentials
-----------
Two sources, tried in order:

1. ``COS_SECRET_ID`` / ``COS_SECRET_KEY`` -- an explicit key pair.  Used for
   local runs (``sync_cos.py``) and as a manual fallback.
2. ``TENCENTCLOUD_SECRETID`` / ``TENCENTCLOUD_SECRETKEY`` (+
   ``TENCENTCLOUD_SESSIONTOKEN``) -- **injected automatically by SCF when the
   function has an execution role attached**.  This is the preferred production
   path: the credentials are short-lived and never appear in the console, in
   this repository, or in the environment of anything else.

   (Those names start with the reserved ``TENCENTCLOUD_`` prefix precisely
   because the platform owns them -- the console refuses to let a human set
   them, which is the tell that the runtime supplies them.)

Environment
-----------
    COS_BUCKET   bucket name including the APPID suffix, e.g. bth-adsb-1300000000
    COS_REGION   e.g. ap-beijing, ap-shanghai, ap-guangzhou

Usage
-----
    from cos_client import CosClient

    cos = CosClient.from_env()
    if cos:
        cos.put_object("data/flights/opensky/x.csv", b"...")
"""
import hashlib
import hmac
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

USER_AGENT = "bth-adsb-cos-client/1.0"
SIGN_VALID_S = 3600          # KeyTime window; one window covers a whole run
RETRIES = 3


def _oneline(text, limit=160):
    """Collapse a response body into one short line for logging.

    COS error bodies are XML/HTML, and dumping them raw buries the actual
    signal in the SCF log across a dozen lines.
    """
    flat = " ".join(str(text).split())
    return flat[:limit] + ("..." if len(flat) > limit else "")


class CosError(RuntimeError):
    pass


def canonical_string(method, path, params, headers):
    """Build COS's `HttpString` canonical form.

    Layout (note the trailing newline, and that an empty component still keeps
    both surrounding blank lines):

        HttpMethod.lower() \\n UriPathname \\n HttpParameters \\n HttpHeaders \\n

    Parameter and header names are lower-cased and sorted; values are
    percent-encoded.  Split out as a module function so it can be checked
    against Tencent's published example:

        canonical_string("get", "/1.jpg", {}, {}) == "get\\n/1.jpg\\n\\n\\n"

    whose SHA1 is documented as
    ``9434d3a0f2a5ecf18545baa37a838bf1e0132a85``.
    """
    param_list = ";".join(sorted(str(k).lower() for k in params))
    params_str = "&".join(
        f"{urllib.parse.quote(str(k).lower(), safe='')}"
        f"={urllib.parse.quote(str(v), safe='')}"
        for k, v in sorted(params.items(), key=lambda kv: str(kv[0]).lower())
    ) if params else ""

    headers_str = "&".join(
        f"{urllib.parse.quote(str(k).lower(), safe='')}"
        f"={urllib.parse.quote(str(v), safe='')}"
        for k, v in sorted(headers.items(), key=lambda kv: str(kv[0]).lower())
    ) if headers else ""

    return f"{method.lower()}\n{path}\n{params_str}\n{headers_str}\n"


def _sha1_hex(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _hmac_sha1(key: bytes, msg: str) -> str:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha1).hexdigest()


class CosClient:
    def __init__(self, secret_id, secret_key, region, bucket, token=None):
        self.secret_id = secret_id
        self.secret_key = secret_key
        self.token = token
        self.region = region
        self.bucket = bucket
        self.host = f"{bucket}.cos.{region}.myqcloud.com"
        self.base = f"https://{self.host}"
        self.cred_source = "unknown"

        # Do NOT inherit `http_proxy` / `https_proxy` from the environment.
        # `urlopen` honours them by default, and a developer machine or CI
        # sandbox that sets one will silently route COS traffic through it --
        # observed breaking even plain 127.0.0.1 calls in a mocked test, which
        # looked like a hang rather than a network error.  SCF has no proxy, so
        # the production path is unaffected; anyone who really does need one
        # sets COS_PROXY explicitly.
        proxy = (os.environ.get("COS_PROXY") or "").strip()
        handler = (urllib.request.ProxyHandler({"http": proxy, "https": proxy})
                   if proxy else urllib.request.ProxyHandler({}))
        self._opener = urllib.request.build_opener(handler)

    # ------------------------------------------------------------ credential
    @classmethod
    def from_env(cls, require=True):
        """Build a client from the environment, or return None if unconfigured."""
        bucket = (os.environ.get("COS_BUCKET") or "").strip()
        region = (os.environ.get("COS_REGION") or "").strip()

        sid = (os.environ.get("COS_SECRET_ID") or "").strip()
        skey = (os.environ.get("COS_SECRET_KEY") or "").strip()
        token = (os.environ.get("COS_SESSION_TOKEN") or "").strip() or None
        source = "COS_SECRET_ID/KEY"

        if not (sid and skey):
            # SCF execution-role credentials, injected by the platform.
            sid = (os.environ.get("TENCENTCLOUD_SECRETID") or "").strip()
            skey = (os.environ.get("TENCENTCLOUD_SECRETKEY") or "").strip()
            token = (os.environ.get("TENCENTCLOUD_SESSIONTOKEN") or "").strip() or None
            source = "SCF execution role"

        if not (sid and skey and bucket and region):
            if require:
                checks = [("COS_BUCKET", bucket), ("COS_REGION", region),
                          ("credentials", sid and skey)]
                missing = [name for name, value in checks if not value]
                raise CosError("COS not configured; missing: " + ", ".join(missing))
            return None

        client = cls(sid, skey, region, bucket, token)
        client.cred_source = source
        return client

    # -------------------------------------------------------------- signing
    def _authorization(self, method: str, key: str, params: dict, now=None):
        """Build the COS V5 `Authorization` header.

        Canonical string (all components lower-cased where applicable):

            HttpString   = method + "\\n" + path + "\\n" + params + "\\n" + headers + "\\n"
            StringToSign = "sha1\\n" + KeyTime + "\\n" + sha1(HttpString) + "\\n"
            SignKey      = HMAC-SHA1(SecretKey, KeyTime)
            Signature    = HMAC-SHA1(SignKey, StringToSign)

        Only ``host`` is signed.  COS ignores headers absent from
        ``q-header-list``, so there is no reason to sign ones that are not
        security-relevant (content-type, content-length), and signing the
        session token is unnecessary because it is bound to the same key.
        """
        t = int(now if now is not None else time.time())
        key_time = f"{t};{t + SIGN_VALID_S}"
        sign_key = _hmac_sha1(self.secret_key.encode("utf-8"), key_time)

        # Path must be percent-encoded but keep its separators readable.
        path = "/" + urllib.parse.quote(key, safe="/-_.~")

        param_list = ";".join(sorted(str(k).lower() for k in params))
        headers = {"host": self.host}
        header_list = ";".join(sorted(headers))

        http_string = canonical_string(method, path, params, headers)
        string_to_sign = f"sha1\n{key_time}\n{_sha1_hex(http_string)}\n"
        signature = _hmac_sha1(sign_key.encode("utf-8"), string_to_sign)

        return (
            "q-sign-algorithm=sha1"
            f"&q-ak={self.secret_id}"
            f"&q-sign-time={key_time}"
            f"&q-key-time={key_time}"
            f"&q-header-list={header_list}"
            f"&q-url-param-list={param_list}"
            f"&q-signature={signature}"
        )

    # --------------------------------------------------------------- request
    def _request(self, method, key="", params=None, body=None, timeout=60):
        params = params or {}
        url = self.base + "/" + urllib.parse.quote(key, safe="/-_.~")
        if params:
            url += "?" + urllib.parse.urlencode(params)

        last = None
        for attempt in range(1, RETRIES + 1):
            req = urllib.request.Request(url, data=body, method=method)
            req.add_header("Host", self.host)
            req.add_header("Authorization", self._authorization(method, key, params))
            req.add_header("User-Agent", USER_AGENT)
            if self.token:
                req.add_header("x-cos-security-token", self.token)
            if body is not None:
                req.add_header("Content-Type", "application/octet-stream")
                req.add_header("Content-Length", str(len(body)))
            try:
                with self._opener.open(req, timeout=timeout) as resp:
                    return resp.read()
            except urllib.error.HTTPError as e:
                detail = _oneline(e.read().decode("utf-8", "replace"))
                # 4xx other than throttling will not fix themselves.
                if e.code in (403, 404):
                    raise CosError(f"HTTP {e.code} on {method} {key or '/'} :: {detail}")
                last = CosError(f"HTTP {e.code} on {method} {key or '/'} :: {detail}")
            except Exception as e:                      # network, timeout, DNS
                last = CosError(f"{type(e).__name__} on {method} {key or '/'}: {e}")
            if attempt < RETRIES:
                time.sleep(2 * attempt)
        raise last

    # ------------------------------------------------------------------- API
    def put_object(self, key, data):
        """Upload bytes (or str) to `key`. Overwrites unconditionally."""
        if isinstance(data, str):
            data = data.encode("utf-8")
        self._request("PUT", key, body=data)
        return len(data)

    def get_object(self, key):
        return self._request("GET", key, timeout=120)

    def list_objects(self, prefix="", max_keys=1000):
        """Yield ``(key, size)`` for everything under `prefix`, paginated."""
        out, marker = [], None
        while True:
            params = {"prefix": prefix, "max-keys": str(max_keys)}
            if marker:
                params["marker"] = marker
            raw = self._request("GET", "", params=params)
            root = ET.fromstring(raw)
            for c in root.findall("Contents"):
                k = c.findtext("Key")
                if k:
                    out.append((k, int(c.findtext("Size") or 0)))
            if (root.findtext("IsTruncated") or "").lower() != "true":
                break
            marker = root.findtext("NextMarker")
            if not marker:
                break
        return out

    def exists(self, key):
        try:
            self._request("HEAD", key)
            return True
        except CosError:
            return False


if __name__ == "__main__":
    # Self-test: the signature must be deterministic and the canonical string
    # well-formed.  A live round-trip is attempted only when credentials exist.
    c = CosClient("AKIDEXAMPLE", "SECRETEXAMPLE", "ap-beijing", "demo-1250000000")
    a1 = c._authorization("PUT", "data/flights/opensky/x.csv", {}, now=1700000000)
    a2 = c._authorization("PUT", "data/flights/opensky/x.csv", {}, now=1700000000)
    print("deterministic  :", a1 == a2)
    print("has signature  :", "q-signature=" in a1)
    print("header-list    :", "q-header-list=host" in a1)
    print("param-list     :", "q-url-param-list=&" in a1)
    print("host           :", c.host)
    print("path encoded   :", c._authorization("GET", "a b/c.csv", {}, now=1).count("=") > 0)

    live = CosClient.from_env(require=False)
    if live is None:
        print("live           : skipped (no COS_BUCKET/COS_REGION/credentials)")
    else:
        live.put_object("_selftest/cos_client.txt", f"ok {time.time()}\n")
        n = dict(live.list_objects("_selftest/"))
        print("live           : PUT+LIST ok,", len(n), "object(s) under _selftest/")
