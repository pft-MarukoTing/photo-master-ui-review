#!/usr/bin/env python3
"""Decrypt the `user_id` query parameter produced by OpenLink_photoMaster.js.

The browser component encrypts appReducer.user.name with AES-256-CBC + PKCS#7
and base64 encodes it, using the same key / IV as
lyric2SongHailuo/aiserver_utils/aescipher.py. This script reverses that.

Usage:
    python3 decrypt_user_id.py                       # decrypt the sample value
    python3 decrypt_user_id.py MmNZsuz813FItZ/lhfy2Bw==
    python3 decrypt_user_id.py 'https://host/index.html?user_id=MmNZ...%3D%3D'
"""

import sys
from base64 import b64decode
from urllib.parse import parse_qs, unquote, urlparse

from Crypto.Cipher import AES

# Must stay in sync with aescipher.py and OpenLink_photoMaster.js.
AES_KEY_B64 = 'I/G8XexufxheijvYVhmY5tPp2RAR5R74LrB6a2mOTbU='
AES_IV_B64 = '+Vi0BGiXCk4jnKw4yyDN4g=='

SAMPLE = 'MmNZsuz813FItZ/lhfy2Bw=='

USER_QUERY_KEY = 'user_id'


def decrypt(enc):
    """Return the plain user name for a base64 AES-256-CBC ciphertext."""
    cipher = AES.new(b64decode(AES_KEY_B64), AES.MODE_CBC, b64decode(AES_IV_B64))
    padded = cipher.decrypt(b64decode(enc))
    if not padded:
        raise ValueError('ciphertext decrypted to an empty block')
    pad_len = padded[-1]
    if pad_len < 1 or pad_len > AES.block_size or padded[-pad_len:] != bytes([pad_len]) * pad_len:
        raise ValueError('bad PKCS#7 padding, wrong key or corrupted ciphertext')
    return padded[:-pad_len].decode('utf-8')


def extract_ciphertext(value):
    """Accept a raw ciphertext, a URL encoded one, or a full target link."""
    value = value.strip()
    if '://' in value or value.startswith('?'):
        query = parse_qs(urlparse(value).query)
        if USER_QUERY_KEY not in query:
            raise ValueError('no %s parameter found in %r' % (USER_QUERY_KEY, value))
        return query[USER_QUERY_KEY][0]
    return unquote(value)


def main(argv):
    values = argv[1:] or [SAMPLE]
    exit_code = 0
    for value in values:
        try:
            ciphertext = extract_ciphertext(value)
            print('%s -> %s' % (ciphertext, decrypt(ciphertext)))
        except Exception as err:
            print('%s -> FAILED: %s' % (value, err), file=sys.stderr)
            exit_code = 1
    return exit_code


if __name__ == '__main__':
    sys.exit(main(sys.argv))
