window.PhotoAgentZip = (() => {
  // Minimal ZIP writer (store method, no compression) -- just enough to
  // bundle a few files client-side without pulling in a third-party lib.
  const CRC_TABLE = (() => {
    const table = new Uint32Array(256);
    for (let n = 0; n < 256; n++) {
      let c = n;
      for (let k = 0; k < 8; k++) {
        c = (c & 1) ? (0xedb88320 ^ (c >>> 1)) : (c >>> 1);
      }
      table[n] = c;
    }
    return table;
  })();

  function crc32(bytes) {
    let crc = 0xffffffff;
    for (let i = 0; i < bytes.length; i++) {
      crc = CRC_TABLE[(crc ^ bytes[i]) & 0xff] ^ (crc >>> 8);
    }
    return (crc ^ 0xffffffff) >>> 0;
  }

  // files: [{ name: string, data: Uint8Array }]
  function createZip(files) {
    const encoder = new TextEncoder();
    const localParts = [];
    const centralParts = [];
    let offset = 0;

    files.forEach(({ name, data }) => {
      const nameBytes = encoder.encode(name);
      const crc = crc32(data);
      const size = data.length;

      const localHeader = new DataView(new ArrayBuffer(30));
      localHeader.setUint32(0, 0x04034b50, true);
      localHeader.setUint16(4, 20, true);   // version needed
      localHeader.setUint16(6, 0, true);    // flags
      localHeader.setUint16(8, 0, true);    // compression: stored
      localHeader.setUint16(10, 0, true);   // mod time
      localHeader.setUint16(12, 0, true);   // mod date
      localHeader.setUint32(14, crc, true);
      localHeader.setUint32(18, size, true); // compressed size
      localHeader.setUint32(22, size, true); // uncompressed size
      localHeader.setUint16(26, nameBytes.length, true);
      localHeader.setUint16(28, 0, true);   // extra field length

      const localEntry = new Uint8Array(30 + nameBytes.length + size);
      localEntry.set(new Uint8Array(localHeader.buffer), 0);
      localEntry.set(nameBytes, 30);
      localEntry.set(data, 30 + nameBytes.length);
      localParts.push(localEntry);

      const centralHeader = new DataView(new ArrayBuffer(46));
      centralHeader.setUint32(0, 0x02014b50, true);
      centralHeader.setUint16(4, 20, true);  // version made by
      centralHeader.setUint16(6, 20, true);  // version needed
      centralHeader.setUint16(8, 0, true);   // flags
      centralHeader.setUint16(10, 0, true);  // compression: stored
      centralHeader.setUint16(12, 0, true);  // mod time
      centralHeader.setUint16(14, 0, true);  // mod date
      centralHeader.setUint32(16, crc, true);
      centralHeader.setUint32(20, size, true);
      centralHeader.setUint32(24, size, true);
      centralHeader.setUint16(28, nameBytes.length, true);
      centralHeader.setUint16(30, 0, true);  // extra field length
      centralHeader.setUint16(32, 0, true);  // comment length
      centralHeader.setUint16(34, 0, true);  // disk number start
      centralHeader.setUint16(36, 0, true);  // internal attrs
      centralHeader.setUint32(38, 0, true);  // external attrs
      centralHeader.setUint32(42, offset, true); // local header offset

      const centralEntry = new Uint8Array(46 + nameBytes.length);
      centralEntry.set(new Uint8Array(centralHeader.buffer), 0);
      centralEntry.set(nameBytes, 46);
      centralParts.push(centralEntry);

      offset += localEntry.length;
    });

    const centralSize = centralParts.reduce((sum, part) => sum + part.length, 0);
    const centralOffset = offset;

    const eocd = new DataView(new ArrayBuffer(22));
    eocd.setUint32(0, 0x06054b50, true);
    eocd.setUint16(4, 0, true);
    eocd.setUint16(6, 0, true);
    eocd.setUint16(8, files.length, true);
    eocd.setUint16(10, files.length, true);
    eocd.setUint32(12, centralSize, true);
    eocd.setUint32(16, centralOffset, true);
    eocd.setUint16(20, 0, true);

    return new Blob([...localParts, ...centralParts, new Uint8Array(eocd.buffer)], {
      type: "application/zip"
    });
  }

  return { createZip };
})();
