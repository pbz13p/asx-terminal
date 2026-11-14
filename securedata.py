"""Encrypt / decrypt private data files with the key in ASX_DATA_KEY (64 hex characters), standard library only.

Scheme (encrypt-then-MAC): keys derived with HMAC-SHA256; keystream = BLAKE2b(key=enc_key, nonce || counter);
tag = HMAC-SHA256(mac_key, header || nonce || ciphertext). File layout: b"ASXE1" + nonce(16) + tag(32) + ciphertext.

  python securedata.py encrypt-dir out     every *.json in out -> *.json.enc (plaintext removed)
  python securedata.py decrypt-dir out     every *.enc in out -> plaintext (the .enc kept)
  python securedata.py encrypt FILE [OUT]  /  decrypt FILE [OUT]
Without a key, encrypt-dir leaves files as they are and prints a warning, so nothing breaks before set-up.
"""
import hashlib
import hmac
import os
import secrets
import sys
from pathlib import Path

MAGIC = b"ASXE1"


def _keys(key=None):
    k = (key or os.environ.get("ASX_DATA_KEY", "")).strip()
    if not k:
        return None
    raw = bytes.fromhex(k)
    if len(raw) < 16:
        raise ValueError("ASX_DATA_KEY too short")
    return hmac.new(raw, b"enc", hashlib.sha256).digest(), hmac.new(raw, b"mac", hashlib.sha256).digest()


def _xor(data, enc_key, nonce):
    out = bytearray()
    block = 1 << 20
    counter = 0
    for start in range(0, len(data), block):
        chunk = data[start:start + block]
        n = (len(chunk) + 63) // 64
        ks = b"".join(hashlib.blake2b(nonce + (counter + i).to_bytes(8, "big"), key=enc_key, digest_size=64).digest()
                      for i in range(n))[:len(chunk)]
        counter += n
        out += (int.from_bytes(chunk, "big") ^ int.from_bytes(ks, "big")).to_bytes(len(chunk), "big")
    return bytes(out)


def encrypt(data, key=None):
    ks = _keys(key)
    if ks is None:
        raise RuntimeError("ASX_DATA_KEY not set")
    nonce = secrets.token_bytes(16)
    ct = _xor(data, ks[0], nonce)
    tag = hmac.new(ks[1], MAGIC + nonce + ct, hashlib.sha256).digest()
    return MAGIC + nonce + tag + ct


def decrypt(blob, key=None):
    ks = _keys(key)
    if ks is None:
        raise RuntimeError("ASX_DATA_KEY not set")
    if blob[:5] != MAGIC:
        raise ValueError("not an encrypted file")
    nonce, tag, ct = blob[5:21], blob[21:53], blob[53:]
    if not hmac.compare_digest(tag, hmac.new(ks[1], MAGIC + nonce + ct, hashlib.sha256).digest()):
        raise ValueError("wrong key or damaged file")
    return _xor(ct, ks[0], nonce)


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "newkey":
        print(secrets.token_hex(32))
        return
    if cmd in ("encrypt-dir", "decrypt-dir"):
        d = Path(argv[2])
        if _keys() is None:
            print("securedata: ASX_DATA_KEY not set -- files left as they are")
            return
        if cmd == "encrypt-dir":
            for f in sorted(d.glob("*.json")):
                (d / (f.name + ".enc")).write_bytes(encrypt(f.read_bytes()))
                f.unlink()
                print("  encrypted", f.name)
        else:
            for f in sorted(d.glob("*.enc")):
                try:
                    (d / f.name[:-4]).write_bytes(decrypt(f.read_bytes()))
                    print("  decrypted", f.name)
                except Exception as e:
                    print("  could not decrypt", f.name, type(e).__name__)
        return
    if cmd in ("encrypt", "decrypt"):
        src = Path(argv[2])
        dst = Path(argv[3]) if len(argv) > 3 else (src.with_name(src.name + ".enc") if cmd == "encrypt" else src.with_suffix(""))
        fn = encrypt if cmd == "encrypt" else decrypt
        dst.write_bytes(fn(src.read_bytes()))
        print(dst)
        return
    print(__doc__)


if __name__ == "__main__":
    main(sys.argv)
