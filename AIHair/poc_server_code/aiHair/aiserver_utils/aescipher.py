from Crypto.Cipher import AES
import base64
from base64 import b64decode


class AESCipher(object):
    def __init__(self, key='I/G8XexufxheijvYVhmY5tPp2RAR5R74LrB6a2mOTbU=',
            iv='+Vi0BGiXCk4jnKw4yyDN4g=='):
        self.bs = AES.block_size
        self.key = b64decode(key)
        self.iv = b64decode(iv)

    def encrypt(self, raw):
        raw = self._pad(raw)
        raw = str.encode(raw)
        cipher = AES.new(self.key, AES.MODE_CBC, self.iv)
        encrypted = cipher.encrypt(raw)
        encoded = base64.b64encode(encrypted)
        return str(encoded, 'utf-8')

    def decrypt(self, enc):
        cipher = AES.new(self.key, AES.MODE_CBC, self.iv)
        return self._unpad(cipher.decrypt(b64decode(enc))).decode('utf-8')

    def _pad(self, s):
        return s + (self.bs - len(s) % self.bs) * chr(self.bs - len(s) % self.bs)

    def _unpad(self, s):
        return s[:-ord(s[len(s)-1:])]